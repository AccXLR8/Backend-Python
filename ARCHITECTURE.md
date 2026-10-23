# Backend Architecture

```
Frontend  ⇄  FastAPI (HTTP / WS)
                  │
                  ▼
           GameService / GameManager
                  │
        chainreaction_py (Python engine)
```

- **FastAPI** handles authentication, routing, WebSockets, matchmaking, persistence, and clocks.
- **ChainReaction Py Engine** remains authoritative for game rules. `GameService` imports the `chainreaction_py` module (installed from the Python-Engine repository) and calls `new_game` / `apply_move` directly.
- **PostgreSQL (SQLAlchemy)** stores users, games, participants, moves, and events. Each state transition is persisted atomically before broadcasts.
- **Redis** supports matchmaking queues, distributed locks, pub/sub, and ephemeral metadata (connections, presence, rate limits).
- **Azure Blob Storage** can store large artifacts (replays, event archives) via `StorageService`.
- **GameManager** enforces per-game serialization to avoid race conditions (move vs timeout).
- **Matchmaking modes** expose both an 8×8 standard queue and a 5×5 mini queue so players can opt into smaller boards (with per-mode clock presets).
- **Clocks** persist turn start timestamps so only the active player's timer counts down (defaults: 5 minutes per player on standard, 10 minutes on mini).
- **WebSockets** deliver authenticated gameplay events (`move_accepted`, `state_snapshot`, `game_finished`, etc.) after verifying the caller’s JWT; clients initiate moves exclusively over the socket channel.
- **Configuration** uses Pydantic settings; secrets come from environment variables.
- **Testing** relies on pytest; integration tests can load the pure-Python engine for parity checks.

Deployment assumes multiple FastAPI instances behind a load balancer with shared Postgres + Redis.
