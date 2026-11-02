# Chain Reaction FastAPI Backend

This backend exposes HTTP and WebSocket APIs for the two-player Chain Reaction engine. It orchestrates matchmaking, clocks, persistence, and communication with the authoritative vendored `game_engine` package.

## Features

- FastAPI application with modular routers
- Health, game, matchmaking, and user endpoints
- Vendored pure-Python Chain Reaction engine (`game_engine`) invoked directly from the backend
- Built-in "standard" (8×8) and "mini" (5×5) matchmaking queues so players can pick their preferred board size
- Chess-style per-player clocks (standard defaults to 5 minutes, mini to 10 minutes) that only run for the active player
- Google Identity sign-in with backend-issued JWT sessions
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

- Browser clients request `POST /api/games/{game_id}/ws-ticket` with an `Authorization: Bearer <JWT>` header, then connect to `ws://<host>/ws/games/{game_id}` using the `chainreaction-v1` and `ticket.<ticket>` WebSocket subprotocols. The ticket expires in 30 seconds and is single-use. Non-browser clients may use an `Authorization: Bearer <JWT>` header directly. Do not put credentials in the URL. Clients should send a `ping` message at least every 30 seconds; connection presence and broadcasts are shared through Redis across application replicas.
- Measure game-server round-trip latency in the client by sending `{"type":"ping","payload":{"ping_id":"unique-id"}}` and timing until the matching `{"type":"pong","payload":{"ping_id":"unique-id"}}`. Legacy pings without an ID remain supported. No database or rating update is performed for a ping.
- The server authenticates the token, verifies the caller is a participant, and sends `connection_ack` + a `state_snapshot` payload.
- Supported client messages today: `ping`, `join_game` (request a fresh snapshot), and `move` (`{"cell": int, "client_move_id": str | null}`).
- Accepted moves persist inside Postgres, advance the authoritative engine state, and trigger broadcast messages: `move_accepted`, `state_snapshot`, and `game_finished` when applicable. Validation errors are returned as `move_rejected` with structured reasons.

## Authentication

- `POST /api/auth/google` — accepts a Google Identity Services ID token, verifies its signature/audience/issuer/expiry and verified email, then returns the backend JWT used by HTTP and WebSocket endpoints.
- `POST /api/auth/register` — creates a registered account and profile. New usernames must be 3–24 ASCII letters, numbers, underscores, or hyphens. New passwords must be 8–128 characters and contain lowercase, uppercase, a number, and a symbol.
- `POST /api/auth/login` — signs in by username (case-insensitive) and password. Existing password hashes remain supported and are upgraded after successful sign-in.
- `POST /api/auth/change-password` — authenticated password change requiring the current password and the same strength rules as registration.
- `POST /api/auth/guest` — creates a guest session; guests cannot use password change or ranked matchmaking.
- Login, Google sign-in, registration, and guest creation are rate-limited per client address using Redis. Password changes are also rate-limited per account. Configure `AUTH_LOGIN_ATTEMPTS_PER_MINUTE`, `AUTH_REGISTRATION_ATTEMPTS_PER_MINUTE`, `AUTH_GUEST_ATTEMPTS_PER_MINUTE`, and `AUTH_PASSWORD_CHANGES_PER_MINUTE`. Configure the ASGI server to trust forwarded client-address headers only from known reverse proxies.
- Set `GOOGLE_CLIENT_ID` on the backend to the same OAuth web client ID used by `NEXT_PUBLIC_GOOGLE_CLIENT_ID` in the frontend.
- Password reset by email is not available yet because local accounts do not collect verified email addresses and the backend has no mail delivery/token flow. Google-only accounts continue to sign in with Google. Password changes do not revoke already-issued bearer tokens; they remain valid for up to 24 hours.
- Every backend token embeds the user id/username and works for both HTTP and WebSocket endpoints.

## Matchmaking

Standard (8×8) board:

- `POST /api/matchmaking/join` — enqueue the caller. As soon as at least two players queue up, the backend randomly pairs them, creates a game, and stores assignments in Redis.
- `GET /api/matchmaking/status` — poll for your assignment; returns the game id once you’ve been paired.
- `POST /api/matchmaking/leave` — remove a waiting player, or return the game ID if matchmaking has already claimed or assigned them. Repeating this request is safe; use `/status` to recover an assignment if the leave response is lost.

Mini (5×5) board:

- `POST /api/matchmaking/mini/join`
- `GET /api/matchmaking/mini/status`
- `POST /api/matchmaking/mini/leave`

Both variants use the same authentication + assignment flow; the mini endpoints always create 5×5 games and allocate 10 minutes per player so frontends can render and display the shorter mode separately. Active queue entries use a 180-second lease, refreshed by status polling or a repeated join; clients should keep polling while waiting.

## Player profiles

- `GET /api/profiles/me` — authenticated profile, statistics, selected avatar and orb skin, owned orb skins, earned achievements, creation date, and profile age.
- `PATCH /api/profiles/me` — update the authenticated player's unique username, nationality, predefined avatar, and/or owned orb skin. Username changes follow the same 3–24 character rules and are checked case-insensitively.
- `GET /api/skins/orbs` — enabled orb skin catalog with stable asset keys for client-side artwork mapping.
- `POST /api/matchmaking/ranked/join`, `GET /api/matchmaking/ranked/status`, and `POST /api/matchmaking/ranked/leave` — registered-account-only 8×8 Flux matchmaking.
- `GET /api/matchmaking/ranked/profile` — ranked win/loss record, placement progress, Flux, and radioactive-element league.

Orb skin compatibility contract: every user, including guests, owns and equips `classic` by default. Inventory and selection are keyed by user ID. `GET /api/games/{id}` and WebSocket `state_snapshot.payload.players` include each player's `orb_skin_id`; clients should treat a missing value on older records or payloads as `classic`.

The skin catalog also returns an `assets` bundle containing versioned preview, texture, detail, and collision-sound URLs. `ORB_SKIN_ASSET_BASE_URL` defaults to the current frontend origin and can be overridden with the HTTPS CDN or frontend origin serving `/orb-skins/{asset_key}/...` files. Enabled skins are expected to publish `preview.webp`, `texture.webp`, `detail.webp`, and `collision.mp3`; the sound is returned as `audio/mpeg`. Increment that skin's `asset_version` when replacing its files so clients can refresh cached assets. Existing `asset_key` and `preview_asset_key` fields remain available.

Accepted REST move responses and WebSocket `move_accepted` payloads include `player_slot`, the authoritative slot that made that move. Use it to associate every reaction step with the mover's equipped skin, even when the final state has already advanced to the opponent's turn.

Example profile fields:

```json
{
  "equipped_orb_skin_id": "classic",
  "owned_orb_skin_ids": ["classic"]
}
```

Example catalog response:

```json
{
  "skins": [{
    "id": "classic",
    "name": "Classic Orb",
    "asset_key": "classic",
    "preview_asset_key": "classic",
    "enabled": true
  }]
}
```

Catalog entries also include the bundled visual and collision sound assets:

```json
{
  "id": "classic",
  "name": "Classic Orb",
  "asset_key": "classic",
  "preview_asset_key": "classic",
  "enabled": true,
  "assets": {
    "version": "1",
    "preview_url": "https://chain-reaction-frontend-ebon.vercel.app/orb-skins/classic/preview.webp?v=1",
    "texture_url": "https://chain-reaction-frontend-ebon.vercel.app/orb-skins/classic/texture.webp?v=1",
    "detail_url": "https://chain-reaction-frontend-ebon.vercel.app/orb-skins/classic/detail.webp?v=1",
    "collision_sound": {
      "url": "https://chain-reaction-frontend-ebon.vercel.app/orb-skins/classic/collision.mp3?v=1",
      "mime_type": "audio/mpeg"
    }
  }
}
```

Example game player and WebSocket snapshot player (the snapshot contains a `players` array with the same player shape):

```json
{
  "user_id": "00000000-0000-0000-0000-000000000001",
  "username": "PlayerOne",
  "player_slot": 0,
  "orb_skin_id": "classic"
}
```

The generated OpenAPI document is available from `/openapi.json` when the service is running.

Ranked Flux is wins divided by losses, capped at 5.00 and rounded half-up to two decimal places. Players are unranked until 10 completed ranked games. Flux leagues use 0.50-wide bands from Technetium (0.00–0.49) through Plutonium (4.50–5.00). A ranked disconnect immediately forfeits the game for the disconnected player; guests receive HTTP 403 from all ranked matchmaking endpoints.
- `GET /api/profiles/{username}` — public profile lookup using a case-insensitive username.
- `GET /api/profiles/avatars` — list enabled predefined avatars.
- `GET /api/profiles/achievements` — list achievement definitions (including locked badges).

Match completion updates matches played, wins, and win streaks atomically. A successful move updates longest-chain waves, and a 20-win streak awards the `WIN_STREAK_20` badge exactly once.

## Environment Variables

See `.env.example` for required configuration: database, Redis, Azure storage, board presets, and clocks. You can customize the standard board via `BOARD_WIDTH`/`BOARD_HEIGHT` and the mini board via `MINI_BOARD_WIDTH`/`MINI_BOARD_HEIGHT`, and you can tune the per-player clocks with `GAME_CLOCK_SECONDS` (standard queue) and `MINI_GAME_CLOCK_SECONDS` (mini queue).

## Development Notes

- The vendored `game_engine` package is the source of truth for game rules; update it and its tests in this repository.
- Alembic migrations live in `app/database/migrations`; the server runs `alembic upgrade head` automatically during startup, so any new database is initialized without manual SQL.
- Redis keys are centralized under `app/redis/keys.py`.

## Architecture Overview

See `ARCHITECTURE.md` for component responsibilities, data flow, and deployment considerations.
