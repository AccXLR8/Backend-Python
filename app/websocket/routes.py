from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi import status as http_status
from pydantic import ValidationError
from sqlalchemy import select

from app.auth.models import User as AuthUser
from app.auth.service import AuthenticationError, auth_service
from app.database import models as db
from app.database.repositories import GameRepository, ParticipantRepository
from app.database.session import Database
from app.games.exceptions import GameNotFoundError
from app.games.models import GameStatus
from app.games.service import game_service
from app.websocket.manager import ws_manager
from app.websocket.protocol import ClientMessageType, MovePayload, ServerMessageType

router = APIRouter()

WS_CODE_UNAUTHORIZED = 4401
WS_CODE_FORBIDDEN = 4403
WS_CODE_NOT_FOUND = 4404


@router.websocket("/ws/games/{game_id}")
async def websocket_games(websocket: WebSocket, game_id: str):
    token = _extract_token(websocket)
    if not token:
        await websocket.close(code=WS_CODE_UNAUTHORIZED)
        return

    try:
        user = await auth_service.authenticate(token)
    except AuthenticationError:
        await websocket.close(code=WS_CODE_UNAUTHORIZED)
        return

    try:
        game_uuid = uuid.UUID(game_id)
    except ValueError:
        await websocket.close(code=WS_CODE_NOT_FOUND)
        return

    try:
        session_factory = Database.session_factory()
    except RuntimeError:
        await websocket.close(code=http_status.WS_1011_INTERNAL_ERROR)
        return

    player_slot = await _resolve_player_slot(session_factory, game_uuid, user.id)
    if player_slot is None:
        await websocket.close(code=WS_CODE_FORBIDDEN)
        return

    await websocket.accept()
    await ws_manager.connect(websocket, user.id, str(game_uuid), player_slot)

    await websocket.send_json(
        {
            "type": ServerMessageType.CONNECTION_ACK,
            "payload": {"game_id": str(game_uuid), "user_id": user.id, "player_slot": player_slot},
        }
    )
    try:
        snapshot = await _build_snapshot(session_factory, game_uuid)
    except GameNotFoundError:
        await websocket.close(code=WS_CODE_NOT_FOUND)
        await ws_manager.disconnect(websocket, str(game_uuid))
        return
    await websocket.send_json({"type": ServerMessageType.STATE_SNAPSHOT, "payload": snapshot})

    try:
        while True:
            data = await websocket.receive_json()
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
                await websocket.send_json({"type": ServerMessageType.PONG, "payload": {}})
                continue

            if client_message == ClientMessageType.JOIN_GAME:
                try:
                    snapshot = await _build_snapshot(session_factory, game_uuid)
                except GameNotFoundError:
                    await websocket.send_json(
                        {
                            "type": ServerMessageType.ERROR,
                            "payload": {"code": "game_not_found", "message": "Game not found"},
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
    except WebSocketDisconnect:
        pass
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
        except Exception as exc:  # pragma: no cover - defensive
            await session.rollback()
            await websocket.send_json(
                {
                    "type": ServerMessageType.ERROR,
                    "payload": {"code": "internal_error", "message": str(exc)},
                }
            )
            return

    message_type = (
        ServerMessageType.MOVE_ACCEPTED if event.event_type == "move" else ServerMessageType.GAME_FINISHED
    )
    message = {"type": message_type, "payload": {"sequence": event.sequence, **event.payload}}
    await ws_manager.broadcast(str(game_id), message)
    try:
        snapshot = await _build_snapshot(session_factory, game_id)
    except GameNotFoundError:
        return
    await ws_manager.broadcast(str(game_id), {"type": ServerMessageType.STATE_SNAPSHOT, "payload": snapshot})
    if event.payload.get("status") == GameStatus.FINISHED.value:
        await ws_manager.broadcast(str(game_id), {"type": ServerMessageType.GAME_FINISHED, "payload": snapshot})


def _extract_token(websocket: WebSocket) -> str | None:
    header = websocket.headers.get("authorization")
    if header and header.lower().startswith("bearer "):
        return header.split(" ", 1)[1]
    return websocket.query_params.get("token")


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
            elapsed_seconds = max(0.0, (datetime.utcnow() - game.active_clock_started_at).total_seconds())
            elapsed_ms = int(elapsed_seconds * 1000)
        players = []
        for participant in participants:
            user = users.get(participant.user_id)
            remaining = participant.remaining_time_ms
            if participant.player_slot == active_slot and elapsed_ms:
                remaining = max(0, remaining - elapsed_ms)
            players.append(
                {
                    "user_id": str(participant.user_id),
                    "username": user.username if user else "unknown",
                    "player_slot": participant.player_slot,
                    "connected": participant.connected,
                    "time_remaining_ms": remaining,
                }
            )
        players.sort(key=lambda payload: payload["player_slot"])
    return {
        "game_id": str(game_id),
        "status": game.status.value,
        "turn_number": game.turn_number,
        "current_player_slot": game.current_player_slot,
        "state": game.latest_state,
        "players": players,
    }
