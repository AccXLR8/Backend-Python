import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.auth.service import auth_service
from app.config.settings import get_settings
from app.database.repositories import UserRepository
from app.database.session import get_session
from app.games.models import GameConfig
from app.games.service import game_service
from app.matchmaking.modes import MatchmakingMode
from app.matchmaking.service import matchmaking_service

router = APIRouter()


@router.post("/join")
async def join_matchmaking(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await _handle_join(user, session, MatchmakingMode.STANDARD)


@router.post("/mini/join")
async def join_mini_matchmaking(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await _handle_join(user, session, MatchmakingMode.MINI)


@router.get("/status")
async def matchmaking_status(user: User = Depends(get_current_user)):
    return await _matchmaking_status(user)


@router.get("/mini/status")
async def matchmaking_status_mini(user: User = Depends(get_current_user)):
    return await _matchmaking_status(user)


@router.post("/leave")
async def leave_matchmaking(user: User = Depends(get_current_user)):
    await matchmaking_service.leave_queue(uuid.UUID(user.id), MatchmakingMode.STANDARD)
    await matchmaking_service.pop_assignment(uuid.UUID(user.id))
    return {"status": "left"}


@router.post("/mini/leave")
async def leave_mini_matchmaking(user: User = Depends(get_current_user)):
    await matchmaking_service.leave_queue(uuid.UUID(user.id), MatchmakingMode.MINI)
    await matchmaking_service.pop_assignment(uuid.UUID(user.id))
    return {"status": "left"}


async def _handle_join(user: User, session: AsyncSession, mode: MatchmakingMode):
    pair = await matchmaking_service.join_queue(uuid.UUID(user.id), mode)
    config = _config_for_mode(mode)

    if pair:
        game = await _create_match_for_pair(session, pair, config=config)
        await session.commit()
        player_ids = [uuid.UUID(pid) for pid in pair]
        await matchmaking_service.save_assignment(player_ids[0], game.id)
        await matchmaking_service.save_assignment(player_ids[1], game.id)
    else:
        await session.commit()

    return await _matchmaking_status(user)


async def _matchmaking_status(user: User):
    assignment = await matchmaking_service.pop_assignment(uuid.UUID(user.id))
    if assignment:
        return {"status": "matched", "game_id": assignment}
    return {"status": "queued"}


def _config_for_mode(mode: MatchmakingMode) -> Optional[GameConfig]:
    settings = get_settings()
    if mode == MatchmakingMode.MINI:
        return GameConfig(
            board_width=settings.mini_board_width,
            board_height=settings.mini_board_height,
            initial_time_ms=settings.mini_game_clock_ms,
        )
    return None


async def _create_match_for_pair(session: AsyncSession, pair: tuple[str, str], config: Optional[GameConfig] = None):
    repo = UserRepository(session)
    player_models = []
    for player_id in pair:
        user_model = await repo.get(uuid.UUID(player_id))
        if not user_model:
            raise HTTPException(status_code=404, detail=f"User {player_id} not found")
        player_models.append(user_model)

    auth_players = [auth_service.model_to_user(model) for model in player_models]
    game = await game_service.create_game(session, auth_players[0], config=config)
    await game_service.join_game(session, game.id, auth_players[1], preferred_slot=1)
    return game
