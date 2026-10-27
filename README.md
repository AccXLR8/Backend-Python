# Chain Reaction FastAPI Backend

This backend exposes HTTP and WebSocket APIs for the two-player Chain Reaction engine. It orchestrates matchmaking, clocks, persistence, and communication with the authoritative vendored `game_engine` package.

## Features

- FastAPI application with modular routers
- Health, game, matchmaking, and user endpoints
- Vendored pure-Python Chain Reaction engine (`game_engine`) invoked directly from the backend
- Built-in "standard" (8×8) and "mini" (5×5) matchmaking queues so players can pick their preferred board size
- Chess-style per-player clocks (standard defaults to 5 minutes, mini to 10 minutes) that only run for the active player
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

## Game engine

The authoritative pure-Python engine is vendored in the top-level `game_engine/` package. It is installed together with the backend by `pip install .`; no second repository checkout or GitHub dependency is required at build or runtime.

`GameService` imports `game_engine` and calls `new_game` and `apply_move` directly. Engine documentation is preserved under `docs/game-engine/`, and its safeguard tests run with the backend test suite under `tests/engine/`.

## Database configuration & migrations

Set `DATABASE_URL` to the Postgres instance you want to use. For Neon, keep the entire value quoted so `&` remains part of the URL, and URL-encode reserved characters in credentials:

```env
DATABASE_URL="postgresql+asyncpg://USER:URL_ENCODED_PASSWORD@HOST/DATABASE?ssl=require"
```

The application automatically runs `alembic upgrade head` during startup (see `app/database/migrations_runner.py`), so connecting it to a fresh Neon database will create all tables without manual SQL. You can still run migrations yourself with `alembic upgrade head` if you prefer to manage them outside the app lifecycle.

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

Standard (8×8) board:

- `POST /api/matchmaking/join` — enqueue the caller. As soon as at least two players queue up, the backend randomly pairs them, creates a game, and stores assignments in Redis.
- `GET /api/matchmaking/status` — poll for your assignment; returns the game id once you’ve been paired.
- `POST /api/matchmaking/leave` — leave the queue and discard any pending assignment.

Mini (5×5) board:

- `POST /api/matchmaking/mini/join`
- `GET /api/matchmaking/mini/status`
- `POST /api/matchmaking/mini/leave`

Both variants use the same authentication + assignment flow; the mini endpoints always create 5×5 games and allocate 10 minutes per player so frontends can render and display the shorter mode separately.

## Environment Variables

See `.env.example` for required configuration: database, Redis, Azure storage, board presets, and clocks. You can customize the standard board via `BOARD_WIDTH`/`BOARD_HEIGHT` and the mini board via `MINI_BOARD_WIDTH`/`MINI_BOARD_HEIGHT`, and you can tune the per-player clocks with `GAME_CLOCK_SECONDS` (standard queue) and `MINI_GAME_CLOCK_SECONDS` (mini queue).

## Development Notes

- The vendored `game_engine` package is the source of truth for game rules; update it and its tests in this repository.
- Alembic migrations live in `app/database/migrations`; the server runs `alembic upgrade head` automatically during startup, so any new database is initialized without manual SQL.
- Redis keys are centralized under `app/redis/keys.py`.

## Architecture Overview

See `ARCHITECTURE.md` for component responsibilities, data flow, and deployment considerations.
