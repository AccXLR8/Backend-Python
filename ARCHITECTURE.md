# Backend Architecture

```
Frontend  ⇄  FastAPI (HTTP / WS)
                  │
                  ▼
           GameService / GameManager
                  │
        EngineAdapter (chainreaction_py)
                  │
        ChainReaction Py Engine
```

- **FastAPI** handles authentication, routing, WebSockets, matchmaking, persistence, and clocks.
- **ChainReaction Py Engine** remains authoritative for game rules. The Python adapter loads the `chainreaction_py` module (installed from the Backend-Python repository) and falls back to the fake adapter only in test/dev scenarios.
- **PostgreSQL (SQLAlchemy)** stores users, games, participants, moves, and events. Each state transition is persisted atomically before broadcasts.
- **Redis** supports matchmaking queues, distributed locks, pub/sub, and ephemeral metadata (connections, presence, rate limits).
- **Azure Blob Storage** can store large artifacts (replays, event archives) via `StorageService`.
- **GameManager** enforces per-game serialization to avoid race conditions (move vs timeout).
- **Clocks** use monotonic time to compute remaining ms. Only the active player's clock runs.
- **WebSockets** deliver authenticated gameplay events (`move_accepted`, `state_snapshot`, `game_finished`, etc.) after verifying the caller’s JWT; clients initiate moves exclusively over the socket channel.
- **Configuration** uses Pydantic settings; secrets come from environment variables.
- **Testing** relies on `FakeEngineAdapter` and pytest. Integration tests can load the pure-Python engine for parity checks.

Deployment assumes multiple FastAPI instances behind a load balancer with shared Postgres + Redis.
