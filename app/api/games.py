from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.database import models as db
from app.database.repositories import GameEventRepository, GameRepository, MoveRepository, ParticipantRepository
from app.database.session import get_session
from app.games.models import FinishReason, GameConfig, GameStatus
from app.games.schemas import (
    CreateGameRequest,
    GameHistoryResponse,
    GamePlayerSchema,
    GameResponse,
    JoinGameRequest,
    MoveRequest,
    MoveResponse,
)
from app.games.service import game_service

router = APIRouter()
logger = logging.getLogger("app.games.api")


@router.post("", response_model=GameResponse, status_code=status.HTTP_201_CREATED)
async def create_game(
    body: CreateGameRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    config = GameConfig(
        board_width=body.board_width,
        board_height=body.board_height,
        initial_time_ms=body.clock_seconds * 1000,
    )
    game = await game_service.create_game(session, user, config)
    await session.commit()
    return await _serialize_game(session, game)


@router.post("/{game_id}/join", response_model=GameResponse)
async def join_game(
    game_id: uuid.UUID,
    body: JoinGameRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    game = await game_service.join_game(session, game_id, user, preferred_slot=body.player_slot)
    await session.commit()
    return await _serialize_game(session, game)


@router.get("/{game_id}", response_model=GameResponse)
async def get_game(game_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    repo = GameRepository(session)
    game = await repo.get(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    return await _serialize_game(session, game)


@router.post("/{game_id}/moves", response_model=MoveResponse)
async def submit_move(
    game_id: uuid.UUID,
    body: MoveRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    try:
        event = await game_service.submit_move(
            session=session,
            game_id=game_id,
            player=user,
            cell=body.cell,
            client_move_id=body.client_move_id,
        )
        await session.commit()
    except HTTPException:
        await session.rollback()
        raise
    except Exception:
        await session.rollback()
        logger.exception(
            "move.request.failed",
            extra={"game_id": str(game_id), "user_id": user.id, "cell": body.cell},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Move could not be processed",
        )
    status_value = event.payload.get("status", GameStatus.ACTIVE.value)
    status_enum = GameStatus(status_value)
    return MoveResponse(
        sequence=event.sequence,
        turn_number=event.payload.get("turn_number", 0),
        player_slot=event.payload.get("player_slot", 0),
        current_player_slot=event.payload.get("current_player_slot"),
        clocks=event.payload.get("clocks", {}),
        reaction=event.payload.get("reaction", {}),
        final_state=event.payload.get("final_state", {}),
        status=status_enum,
    )


@router.get("/{game_id}/history", response_model=GameHistoryResponse)
async def game_history(game_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    repo = GameRepository(session)
    game = await repo.get(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    move_repo = MoveRepository(session)
    move_rows = await move_repo.list_for_game(game_id)
    event_repo = GameEventRepository(session)
    events = await event_repo.list_events(game_id)
    events_by_sequence = {event.sequence: event.payload for event in events}

    serialized_moves = []
    for move in move_rows:
        payload = events_by_sequence.get(move.sequence, {})
        serialized_moves.append(
            {
                "sequence": move.sequence,
                "cell": move.cell,
                "player_slot": move.player_slot,
                "turn_number": move.turn_number,
                "reaction": payload.get("reaction"),
                "final_state": payload.get("final_state"),
                "client_move_id": move.client_move_id,
                "time_remaining_ms": move.time_remaining_ms,
            }
        )

    result = FinishReason(game.finish_reason) if game.finish_reason else None
    return GameHistoryResponse(game_id=game_id, moves=serialized_moves, result=result)


async def _serialize_game(session: AsyncSession, game: db.GameModel) -> GameResponse:
    participant_repo = ParticipantRepository(session)
    participants = await participant_repo.list_for_game(game.id)
    user_ids = [participant.user_id for participant in participants]
    users_by_id: dict[uuid.UUID, db.UserModel] = {}
    if user_ids:
        result = await session.execute(select(db.UserModel).where(db.UserModel.id.in_(user_ids)))
        users_by_id = {user.id: user for user in result.scalars().all()}

    players = []
    for participant in sorted(participants, key=lambda p: p.player_slot):
        user = users_by_id.get(participant.user_id)
        players.append(
            GamePlayerSchema(
                user_id=participant.user_id,
                username=user.username if user else "unknown",
                player_slot=participant.player_slot,
                connected=participant.connected,
                time_remaining_ms=participant.remaining_time_ms,
            )
        )

    return GameResponse(
        id=game.id,
        status=game.status,
        players=players,
        turn_number=game.turn_number,
        current_player_slot=game.current_player_slot,
        created_at=game.created_at,
        updated_at=game.updated_at,
    )
