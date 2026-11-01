from __future__ import annotations

import uuid
import hashlib
import json
from datetime import datetime, timezone
import logging

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi import status as http_status
from pydantic import ValidationError
from sqlalchemy import select

from app.auth.models import User as AuthUser
from app.auth.service import AuthenticationError, auth_service
from app.database import models as db
from app.database.repositories import GameRepository, ParticipantRepository, UserRepository
from app.database.session import Database
from app.games.exceptions import GameNotFoundError
from app.games.models import GameStatus
from app.games.service import game_service
from app.rate_limit import (
    enforce_move_rate_limit,
    enforce_websocket_connect_rate_limit,
    enforce_websocket_message_rate_limit,
    enforce_websocket_snapshot_rate_limit,
)
from app.redis.client import RedisClient
from app.redis.keys import websocket_ticket_key
from app.websocket.manager import ws_manager
from app.websocket.protocol import ClientMessageType, MovePayload, ServerMessageType

router = APIRouter()
logger = logging.getLogger("app.websocket")

WS_CODE_UNAUTHORIZED = 4401
WS_CODE_FORBIDDEN = 4403
WS_CODE_NOT_FOUND = 4404
WS_CODE_RATE_LIMITED = 4429
_CONSUME_TICKET_SCRIPT = """
local value = redis.call('GET', KEYS[1])
if value then redis.call('DEL', KEYS[1]) end
return value
"""


@router.websocket("/ws/games/{game_id}")
async def websocket_games(websocket: WebSocket, game_id: str):
    logger.info("ws.connect.attempt", extra={"game_id": game_id})
    try:
        game_uuid = uuid.UUID(game_id)
    except ValueError:
        logger.warning("ws.connect.invalid_game_id", extra={"game_id": game_id})
        await websocket.close(code=WS_CODE_NOT_FOUND)
        return

    try:
        session_factory = Database.session_factory()
    except RuntimeError:
        logger.exception("ws.connect.session_unavailable", extra={"game_id": game_id})
        await websocket.close(code=http_status.WS_1011_INTERNAL_ERROR)
        return

    token = _extract_token(websocket)
    socket_ticket = _extract_socket_ticket(websocket)
    if token:
        try:
            user = await auth_service.authenticate(token)
        except AuthenticationError:
            logger.warning("ws.connect.auth_failed", extra={"game_id": game_id})
            await websocket.close(code=WS_CODE_UNAUTHORIZED)
            return
    elif socket_ticket:
        try:
            user = await _consume_socket_ticket(session_factory, socket_ticket, game_uuid)
        except Exception:
            logger.exception("ws.connect.ticket_store_failed", extra={"game_id": str(game_uuid)})
            await websocket.close(code=1013, reason="authentication service unavailable")
            return
        if user is None:
            logger.warning("ws.connect.ticket_failed", extra={"game_id": game_id})
            await websocket.close(code=WS_CODE_UNAUTHORIZED)
            return
    else:
        logger.warning("ws.connect.missing_credentials", extra={"game_id": game_id})
        await websocket.close(code=WS_CODE_UNAUTHORIZED)
        return

    try:
        await enforce_websocket_connect_rate_limit(user.id)
    except HTTPException as exc:
        await websocket.close(
            code=WS_CODE_RATE_LIMITED if exc.status_code == 429 else 1013,
            reason="connection rate limit exceeded",
        )
        return

    player_slot = await _resolve_player_slot(session_factory, game_uuid, user.id)
    if player_slot is None:
        logger.warning(
            "ws.connect.player_forbidden",
            extra={"game_id": str(game_uuid), "user_id": user.id},
        )
        await websocket.close(code=WS_CODE_FORBIDDEN)
        return

    try:
        await enforce_websocket_snapshot_rate_limit(user.id)
    except HTTPException as exc:
        await websocket.close(
            code=WS_CODE_RATE_LIMITED if exc.status_code == 429 else 1013,
            reason="snapshot rate limit exceeded",
        )
        return

    offered_protocols = _offered_subprotocols(websocket)
    if socket_ticket and "chainreaction-v1" not in offered_protocols:
        await websocket.close(code=WS_CODE_UNAUTHORIZED)
        return
    await websocket.accept(subprotocol="chainreaction-v1" if socket_ticket else None)
    logger.info(
        "ws.connect.accepted",
        extra={"game_id": str(game_uuid), "user_id": user.id, "player_slot": player_slot},
    )
    try:
        connected = await ws_manager.connect(websocket, user.id, str(game_uuid), player_slot)
    except Exception:
        logger.exception("ws.connect.presence_failed", extra={"game_id": str(game_uuid), "user_id": user.id})
        await websocket.close(code=http_status.WS_1011_INTERNAL_ERROR)
        return
    if not connected:
        await websocket.close(code=WS_CODE_RATE_LIMITED, reason="connection limit exceeded")
        return

    try:
        async with session_factory() as session:
            await game_service.set_player_connected(session, game_uuid, uuid.UUID(user.id), True)
            await session.commit()
    except Exception:
        logger.exception("ws.connect.presence_persist_failed", extra={"game_id": str(game_uuid), "user_id": user.id})
        await ws_manager.disconnect(websocket, str(game_uuid))
        await websocket.close(code=http_status.WS_1011_INTERNAL_ERROR)
        return

    try:
        await websocket.send_json(
            {
                "type": ServerMessageType.CONNECTION_ACK,
                "payload": {"game_id": str(game_uuid), "user_id": user.id, "player_slot": player_slot},
            }
        )
        snapshot = await _build_snapshot(session_factory, game_uuid)
    except GameNotFoundError:
        logger.warning(
            "ws.snapshot.not_found",
            extra={"game_id": str(game_uuid), "user_id": user.id},
        )
        await websocket.close(code=WS_CODE_NOT_FOUND)
        await ws_manager.disconnect(websocket, str(game_uuid))
        return
    except HTTPException as exc:
        await websocket.send_json(
            {"type": ServerMessageType.ERROR, "payload": {"code": "rate_limited", "message": exc.detail}}
        )
        await websocket.close(code=WS_CODE_RATE_LIMITED, reason="snapshot rate limit exceeded")
        await ws_manager.disconnect(websocket, str(game_uuid))
        return
    except Exception:
        logger.exception(
            "ws.snapshot.failed",
            extra={"game_id": str(game_uuid), "user_id": user.id},
        )
        await websocket.close(code=http_status.WS_1011_INTERNAL_ERROR)
        await ws_manager.disconnect(websocket, str(game_uuid))
        return
    try:
        await websocket.send_json({"type": ServerMessageType.STATE_SNAPSHOT, "payload": snapshot})
    except Exception:
        await ws_manager.disconnect(websocket, str(game_uuid))
        return

    try:
        while True:
            try:
                data = await websocket.receive_json()
            except ValueError:
                try:
                    await enforce_websocket_message_rate_limit(user.id)
                    await ws_manager.heartbeat(str(game_uuid), player_slot, user.id)
                except HTTPException:
                    await websocket.close(code=WS_CODE_RATE_LIMITED, reason="rate limit exceeded")
                    break
                logger.warning(
                    "ws.receive.invalid_json",
                    extra={"game_id": str(game_uuid), "user_id": user.id},
                )
                await websocket.send_json(
                    {
                        "type": ServerMessageType.ERROR,
                        "payload": {"code": "invalid_json", "message": "Malformed JSON payload"},
                    }
                )
                continue
            try:
                await enforce_websocket_message_rate_limit(user.id)
            except HTTPException as exc:
                code = "rate_limited" if exc.status_code == 429 else "rate_limiter_unavailable"
                await websocket.send_json(
                    {"type": ServerMessageType.ERROR, "payload": {"code": code, "message": exc.detail}}
                )
                await websocket.close(
                    code=WS_CODE_RATE_LIMITED if exc.status_code == 429 else 1013,
                    reason=code,
                )
                break
            await ws_manager.heartbeat(str(game_uuid), player_slot, user.id)
            if not isinstance(data, dict):
                await websocket.send_json(
                    {
                        "type": ServerMessageType.ERROR,
                        "payload": {"code": "invalid_payload", "message": "Expected a JSON object"},
                    }
                )
                continue
            message_type = data.get("type")
            try:
                client_message = ClientMessageType(message_type)
            except ValueError:
                await websocket.send_json(
                    {
                        "type": ServerMessageType.ERROR,
                        "payload": {"code": "invalid_type", "message": f"Unsupported type: {message_type}"},
                    }
                )
                continue

            if client_message == ClientMessageType.PING:
                ping_payload = data.get("payload")
                ping_id = ping_payload.get("ping_id") if isinstance(ping_payload, dict) else None
                # Echo a bounded client ID so the frontend can calculate RTT
                # with its own monotonic clock. Empty/legacy pings stay valid.
                pong_payload = (
                    {"ping_id": ping_id}
                    if isinstance(ping_id, str) and 0 < len(ping_id) <= 64
                    else {}
                )
                await websocket.send_json(
                    {"type": ServerMessageType.PONG, "payload": pong_payload}
                )
                continue

            if client_message == ClientMessageType.JOIN_GAME:
                try:
                    await enforce_websocket_snapshot_rate_limit(user.id)
                    snapshot = await _build_snapshot(session_factory, game_uuid)
                except HTTPException as exc:
                    await websocket.send_json(
                        {
                            "type": ServerMessageType.ERROR,
                            "payload": {"code": "rate_limited", "message": exc.detail},
                        }
                    )
                    continue
                except GameNotFoundError:
                    await websocket.send_json(
                        {
                            "type": ServerMessageType.ERROR,
                            "payload": {"code": "game_not_found", "message": "Game not found"},
                        }
                    )
                    continue
                except Exception:
                    logger.exception(
                        "ws.snapshot.failed",
                        extra={"game_id": str(game_uuid), "user_id": user.id},
                    )
                    await websocket.send_json(
                        {
                            "type": ServerMessageType.ERROR,
                            "payload": {"code": "snapshot_error", "message": "Unable to build snapshot"},
                        }
                    )
                    continue
                await websocket.send_json({"type": ServerMessageType.STATE_SNAPSHOT, "payload": snapshot})
                continue

            if client_message == ClientMessageType.MOVE:
                await _handle_move_message(websocket, session_factory, game_uuid, user, data)
                continue

            await websocket.send_json(
                {
                    "type": ServerMessageType.ERROR,
                    "payload": {"code": "not_supported", "message": f"{client_message.value} not supported"},
                }
            )
    except WebSocketDisconnect as exc:
        logger.info(
            "ws.disconnect.client",
            extra={
                "game_id": str(game_uuid),
                "user_id": user.id,
                "close_code": exc.code,
                "close_reason": exc.reason or "",
            },
        )
    except Exception:
        logger.exception(
            "ws.loop.failed",
            extra={"game_id": str(game_uuid), "user_id": user.id},
        )
        await websocket.close(code=http_status.WS_1011_INTERNAL_ERROR)
    finally:
        await ws_manager.disconnect(websocket, str(game_uuid))


async def _handle_move_message(
    websocket: WebSocket,
    session_factory,
    game_id: uuid.UUID,
    user: AuthUser,
    raw_message: dict,
) -> None:
    try:
        payload = MovePayload.model_validate(raw_message.get("payload") or {})
    except ValidationError as exc:
        await websocket.send_json(
            {
                "type": ServerMessageType.MOVE_REJECTED,
                "payload": {"code": "invalid_payload", "message": exc.errors()},
            }
        )
        return

    async with session_factory() as session:
        try:
            await enforce_move_rate_limit(user.id)
            event = await game_service.submit_move(
                session=session,
                game_id=game_id,
                player=user,
                cell=payload.cell,
                client_move_id=payload.client_move_id,
            )
            await session.commit()
        except HTTPException as exc:
            await session.rollback()
            await websocket.send_json(
                {
                    "type": ServerMessageType.MOVE_REJECTED,
                    "payload": {"code": exc.status_code, "message": exc.detail},
                }
            )
            return
        except Exception:  # pragma: no cover - defensive
            await session.rollback()
            logger.exception(
                "ws.move.failed",
                extra={"game_id": str(game_id), "user_id": user.id, "cell": payload.cell},
            )
            await websocket.send_json(
                {
                    "type": ServerMessageType.ERROR,
                    "payload": {"code": "internal_error", "message": "Move could not be processed"},
                }
            )
            return

    message_type = (
        ServerMessageType.MOVE_ACCEPTED if event.event_type == "move" else ServerMessageType.GAME_FINISHED
    )
    message = {"type": message_type, "payload": {"sequence": event.sequence, **event.payload}}
    # The accepted-move payload already contains the authoritative final state,
    # next player, outcome and clocks. Re-querying the database for a snapshot
    # here added three remote round trips after every move. Full snapshots remain
    # available on connect/reconnect and explicit join_game requests.
    await ws_manager.broadcast(str(game_id), message)


def _extract_token(websocket: WebSocket) -> str | None:
    header = websocket.headers.get("authorization")
    if header and header.lower().startswith("bearer "):
        return header.split(" ", 1)[1]
    return None


def _offered_subprotocols(websocket: WebSocket) -> list[str]:
    return [value.strip() for value in websocket.headers.get("sec-websocket-protocol", "").split(",") if value.strip()]


def _extract_socket_ticket(websocket: WebSocket) -> str | None:
    for protocol in _offered_subprotocols(websocket):
        if protocol.startswith("ticket.") and len(protocol) > len("ticket."):
            return protocol[len("ticket."):]
    return None


async def _consume_socket_ticket(session_factory, ticket: str, expected_game_id: uuid.UUID) -> AuthUser | None:
    ticket_hash = hashlib.sha256(ticket.encode("utf-8")).hexdigest()
    value = await RedisClient.instance().eval(
        _CONSUME_TICKET_SCRIPT, 1, websocket_ticket_key(ticket_hash)
    )
    if value is None:
        return None
    try:
        claims = json.loads(value)
        if claims.get("game_id") != str(expected_game_id):
            return None
        user_id = uuid.UUID(claims["user_id"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
    async with session_factory() as session:
        user_model = await UserRepository(session).get(user_id)
        if user_model is None:
            return None
        return AuthUser(
            id=str(user_model.id),
            username=user_model.username,
            created_at=user_model.created_at,
            updated_at=user_model.updated_at,
            is_guest=user_model.is_guest,
            nationality=getattr(user_model, "nationality", "global") or "global",
        )


async def _handle_final_disconnect(game_id: str, user_id: str) -> None:
    # A reconnect may have registered on this or another replica while the old
    # socket was closing. Only the final shared presence removal reaches here.
    if await ws_manager.has_player_connection(game_id, user_id):
        return
    session_factory = Database.session_factory()
    async with session_factory() as session:
        event = await game_service.forfeit_disconnected_player(
            session,
            uuid.UUID(game_id),
            uuid.UUID(user_id),
        )
        await session.commit()
    if event is not None:
        await ws_manager.broadcast(
            game_id,
            {
                "type": ServerMessageType.GAME_FINISHED,
                "payload": {"sequence": event.sequence, **event.payload},
            },
        )


ws_manager.set_disconnect_callback(_handle_final_disconnect)


async def _resolve_player_slot(session_factory, game_id: uuid.UUID, user_id: str) -> int | None:
    async with session_factory() as session:
        repo = GameRepository(session)
        game = await repo.get(game_id)
        if not game:
            return None
        try:
            uid = uuid.UUID(user_id)
        except ValueError:
            return None
        if game.player_1_id == uid:
            return 0
        if game.player_2_id == uid:
            return 1
    return None


async def _build_snapshot(session_factory, game_id: uuid.UUID) -> dict:
    async with session_factory() as session:
        repo = GameRepository(session)
        game = await repo.get(game_id)
        if not game:
            raise GameNotFoundError()
        participant_repo = ParticipantRepository(session)
        participants = await participant_repo.list_for_game(game_id)
        user_ids = [participant.user_id for participant in participants]
        users: dict[uuid.UUID, db.UserModel] = {}
        if user_ids:
            result = await session.execute(select(db.UserModel).where(db.UserModel.id.in_(user_ids)))
            users = {row.id: row for row in result.scalars().all()}
        active_slot = game.current_player_slot
        elapsed_ms = 0
        if active_slot is not None and game.active_clock_started_at and game.status in {GameStatus.READY, GameStatus.ACTIVE}:
            now_utc = datetime.now(timezone.utc)
            started_at = game.active_clock_started_at
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            elapsed_seconds = max(0.0, (now_utc - started_at).total_seconds())
            elapsed_ms = int(elapsed_seconds * 1000)
        players = []
        selections = {}
        if user_ids:
            selection_rows = (await session.execute(select(db.UserOrbSkinSelectionModel).where(
                db.UserOrbSkinSelectionModel.user_id.in_(user_ids)))).scalars().all()
            selections = {row.user_id: row.skin_id for row in selection_rows}
        for participant in participants:
            user = users.get(participant.user_id)
            remaining = participant.remaining_time_ms
            if participant.player_slot == active_slot and elapsed_ms:
                remaining = max(0, remaining - elapsed_ms)
            try:
                connected = await ws_manager.has_player_connection(
                    str(game_id), str(participant.user_id)
                )
            except Exception:
                logger.exception(
                    "ws.snapshot.presence_read_failed",
                    extra={"game_id": str(game_id), "user_id": str(participant.user_id)},
                )
                connected = participant.connected
            players.append(
                {
                    "user_id": str(participant.user_id),
                    "username": user.username if user else "unknown",
                    "player_slot": participant.player_slot,
                    "orb_skin_id": selections.get(participant.user_id, "classic"),
                    "nationality": (getattr(user, "nationality", None) or "global") if user else "global",
                    "connected": connected,
                    "time_remaining_ms": remaining,
                }
            )
        players.sort(key=lambda payload: payload["player_slot"])
    return {
        "game_id": str(game_id),
        "status": game.status.value,
        "is_ranked": game.is_ranked,
        "turn_number": game.turn_number,
        "current_player_slot": game.current_player_slot,
        "state": game.latest_state,
        "players": players,
    }
