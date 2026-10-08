# Chain Reaction FastAPI Backend

This backend exposes HTTP and WebSocket APIs for the two-player Chain Reaction engine. It orchestrates matchmaking, clocks, persistence, and communication with the authoritative pure-Python engine shipped as the `chainreaction_py` library.

## Features

- FastAPI application with modular routers
- Health, game, matchmaking, and user endpoints
- Pure-Python Chain Reaction engine bindings (via the `chainreaction_py` module) with a fake adapter fallback for tests
- Secure JWT authentication with built-in username/password registration + guest tokens
- SQLAlchemy models for users, games, participants, moves, and events backed by Postgres
- Redis integration for matchmaking queues and distributed locks
- Azure Blob storage abstraction for replays
- Request ID & structured logging middleware
- Pydantic settings loaded from `.env`
- Real-time gameplay orchestration over WebSockets with persisted moves and clock state
- Dockerfile & pyproject

## Quickstart

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
cp .env.example .env
uvicorn app.main:app --reload
```

Run tests:

```bash
pytest
```

## Engine bindings

The backend loads the module named by `ENGINE_MODULE` (defaults to `chainreaction_py`). Install the pure-Python engine from the [`Backend-Python`](https://github.com/AccXLR8/Backend-Python) repository:

```bash
git clone https://github.com/AccXLR8/Backend-Python.git
python -m pip install Backend-Python/python-engine
```

You can also rely on a direct URL install:

```bash
python -m pip install "git+https://github.com/AccXLR8/Backend-Python@main#subdirectory=python-engine"
```

Once the module is importable the FastAPI app can call `new_game`/`apply_move` directly. If the module cannot be imported the service falls back to the deterministic fake adapter, so be sure to install the real engine in production.

## WebSocket gameplay flow

- Clients connect to `ws://<host>/ws/games/{game_id}?token=<JWT>` (or supply `Authorization: Bearer <JWT>`).
- The server authenticates the token, verifies the caller is a participant, and sends `connection_ack` + a `state_snapshot` payload.
- Supported client messages today: `ping`, `join_game` (request a fresh snapshot), and `move` (`{"cell": int, "client_move_id": str | null}`).
- Accepted moves persist inside Postgres, advance the authoritative engine state, and trigger broadcast messages: `move_accepted`, `state_snapshot`, and `game_finished` when applicable. Validation errors are returned as `move_rejected` with structured reasons.

## Authentication & guest mode

- `POST /api/auth/register` — create a username/password account; passwords are hashed with bcrypt and stored in Postgres.
- `POST /api/auth/login` — obtain a JWT using stored credentials.
- `POST /api/auth/guest` — mint a disposable guest identity (no password required) that still produces a signed JWT.
- Every token embeds the user id/username and works for both HTTP and WebSocket endpoints.

## Matchmaking

- `POST /api/matchmaking/join` — enqueue the caller. As soon as at least two players queue up, the backend randomly pairs them, creates a game, and stores assignments in Redis.
- `GET /api/matchmaking/status` — poll for your assignment; returns the game id once you’ve been paired.
- `POST /api/matchmaking/leave` — leave the queue and discard any pending assignment.

## Environment Variables

See `.env.example` for required configuration: database, Redis, Azure storage, engine module, etc.

## Development Notes

- The ChainReaction Py Engine is injected via `ENGINE_MODULE`. Install `chainreaction_py` from the Backend-Python repository (or another compatible module) before starting the API service.
- Alembic migrations live in `app/database/migrations`; the server runs `alembic upgrade head` automatically during startup, so any new database is initialized without manual SQL.
- Redis keys are centralized under `app/redis/keys.py`.

## Architecture Overview

See `ARCHITECTURE.md` for component responsibilities, data flow, and deployment considerations.
