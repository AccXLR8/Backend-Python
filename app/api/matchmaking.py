import uuid
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.auth.service import auth_service
from app.config.settings import get_settings
from app.competitive.service import PLACEMENT_GAMES, competitive_service
from app.database import models as db
from app.database.repositories import GameRepository, UserRepository
from app.database.session import Database, get_session
from app.games.models import GameConfig
from app.games.service import game_service
from app.matchmaking.modes import MatchmakingMode
from app.matchmaking.service import matchmaking_service

router = APIRouter()
logger = logging.getLogger("app.matchmaking.api")


def _require_registered_player(user: User) -> None:
    if user.is_guest:
        raise HTTPException(status_code=403, detail="Ranked matchmaking is only available to registered players")


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
async def matchmaking_status(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)
):
    return await _matchmaking_status(user, session, MatchmakingMode.STANDARD)


@router.get("/mini/status")
async def matchmaking_status_mini(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)
):
    return await _matchmaking_status(user, session, MatchmakingMode.MINI)


@router.post("/leave")
async def leave_matchmaking(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)
):
    state, game_id = await matchmaking_service.leave_queue(uuid.UUID(user.id), MatchmakingMode.STANDARD)
    if game_id:
        return {"status": "matched", "game_id": game_id}
    if state == "matching":
        game_id = await _active_match_id(session, uuid.UUID(user.id), MatchmakingMode.STANDARD.value)
        if game_id:
            return {"status": "matched", "game_id": str(game_id)}
        raise HTTPException(status_code=503, detail="Match creation is in progress; retry matchmaking status")
    game_id = await _active_match_id(session, uuid.UUID(user.id), MatchmakingMode.STANDARD.value)
    if game_id:
        return {"status": "matched", "game_id": str(game_id)}
    return {"status": "left"}


@router.post("/mini/leave")
async def leave_mini_matchmaking(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)
):
    state, game_id = await matchmaking_service.leave_queue(uuid.UUID(user.id), MatchmakingMode.MINI)
    if game_id:
        return {"status": "matched", "game_id": game_id}
    if state == "matching":
        game_id = await _active_match_id(session, uuid.UUID(user.id), MatchmakingMode.MINI.value)
        if game_id:
            return {"status": "matched", "game_id": str(game_id)}
        raise HTTPException(status_code=503, detail="Match creation is in progress; retry matchmaking status")
    game_id = await _active_match_id(session, uuid.UUID(user.id), MatchmakingMode.MINI.value)
    if game_id:
        return {"status": "matched", "game_id": str(game_id)}
    return {"status": "left"}


@router.post("/ranked/join")
async def join_ranked_matchmaking(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    _require_registered_player(user)
    user_id = uuid.UUID(user.id)
    existing_match = await GameRepository(session).find_active_match_for_user(user_id)
    if existing_match is None:
        await _clear_finished_assignment(session, user_id)
        legacy_match = await _legacy_assigned_match(session, user_id)
        existing_match = legacy_match[0] if legacy_match else None
    if existing_match:
        existing_mode = existing_match.matchmaking_mode or (
            "ranked" if existing_match.is_ranked else _infer_legacy_mode(existing_match)
        )
        if existing_mode == "ranked":
            return {"status": "matched", "game_id": str(existing_match.id)}
        raise HTTPException(status_code=409, detail="Already assigned to a matchmaking game in another mode")

    rating = await competitive_service.get_or_create_rating(session, user_id)
    flux = float(rating.flux) if rating.wins + rating.losses >= PLACEMENT_GAMES else None
    pair_claim = await matchmaking_service.join_ranked(user_id, flux)
    if pair_claim:
        pair = (pair_claim[0], pair_claim[1])
        claim = pair_claim[2]
        settings = get_settings()
        config = GameConfig(
            board_width=8,
            board_height=8,
            initial_time_ms=settings.game_clock_ms,
        )
        try:
            game = await _create_match_for_pair(
                session,
                pair,
                config=config,
                is_ranked=True,
                matchmaking_mode="ranked",
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            recovered_id = None
            try:
                async with Database.session_factory()() as recovery_session:
                    recovered = await GameRepository(recovery_session).find_active_match_for_pair(
                        (uuid.UUID(pair[0]), uuid.UUID(pair[1])), "ranked"
                    )
                    recovered_id = recovered.id if recovered else None
            except Exception:
                logger.exception("matchmaking.recovery_lookup_failed", extra={"mode": "ranked"})
            if recovered_id is None:
                blocked_users = await _active_pair_users(pair)
                try:
                    await matchmaking_service.release_pair(pair, "ranked", claim, blocked_users)
                except Exception:
                    logger.exception("matchmaking.claim_release_failed", extra={"mode": "ranked"})
                logger.exception("matchmaking.game_creation_failed", extra={"mode": "ranked"})
                raise HTTPException(status_code=503, detail="Match creation failed; players were returned to the queue") from exc
            game_id = recovered_id
        else:
            game_id = game.id
        try:
            saved = await matchmaking_service.save_pair_assignment(
                [uuid.UUID(pid) for pid in pair], game_id, "ranked", claim
            )
            if not saved:
                logger.warning("matchmaking.assignment_claim_expired", extra={"game_id": str(game_id)})
        except Exception:
            logger.exception("matchmaking.assignment_save_failed", extra={"game_id": str(game_id)})
    else:
        await session.commit()
    return await _ranked_matchmaking_status(user, session)


@router.get("/ranked/status")
async def ranked_matchmaking_status(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)
):
    _require_registered_player(user)
    return await _ranked_matchmaking_status(user, session)


@router.post("/ranked/leave")
async def leave_ranked_matchmaking(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)
):
    _require_registered_player(user)
    state, game_id = await matchmaking_service.leave_ranked(uuid.UUID(user.id))
    if game_id:
        return {"status": "matched", "game_id": game_id}
    if state == "matching":
        game_id = await _active_match_id(session, uuid.UUID(user.id), "ranked")
        if game_id:
            return {"status": "matched", "game_id": str(game_id)}
        raise HTTPException(status_code=503, detail="Match creation is in progress; retry matchmaking status")
    game_id = await _active_match_id(session, uuid.UUID(user.id), "ranked")
    if game_id:
        return {"status": "matched", "game_id": str(game_id)}
    return {"status": "left"}


@router.get("/ranked/profile")
async def ranked_profile(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    _require_registered_player(user)
    result = await competitive_service.profile(session, uuid.UUID(user.id))
    await session.commit()
    return result


async def _handle_join(user: User, session: AsyncSession, mode: MatchmakingMode):
    user_id = uuid.UUID(user.id)
    existing_match = await GameRepository(session).find_active_match_for_user(user_id)
    if existing_match is None:
        await _clear_finished_assignment(session, user_id)
        legacy_match = await _legacy_assigned_match(session, user_id)
        existing_match = legacy_match[0] if legacy_match else None
    if existing_match:
        existing_mode = existing_match.matchmaking_mode or (
            "ranked" if existing_match.is_ranked else _infer_legacy_mode(existing_match)
        )
        if existing_mode == mode.value:
            return {"status": "matched", "game_id": str(existing_match.id)}
        raise HTTPException(status_code=409, detail="Already assigned to a matchmaking game in another mode")

    pair_claim = await matchmaking_service.join_queue(user_id, mode)
    config = _config_for_mode(mode)

    if pair_claim:
        pair = (pair_claim[0], pair_claim[1])
        claim = pair_claim[2]
        try:
            game = await _create_match_for_pair(session, pair, config=config, matchmaking_mode=mode.value)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            # A commit can fail after the database accepted it. Reconcile by
            # exact pair before releasing the Redis claim back to the queue.
            recovered_id = None
            try:
                async with Database.session_factory()() as recovery_session:
                    recovered = await GameRepository(recovery_session).find_active_match_for_pair(
                        (uuid.UUID(pair[0]), uuid.UUID(pair[1])), mode.value
                    )
                    recovered_id = recovered.id if recovered else None
            except Exception:
                logger.exception("matchmaking.recovery_lookup_failed", extra={"mode": mode.value})
            if recovered_id is None:
                blocked_users = await _active_pair_users(pair)
                try:
                    await matchmaking_service.release_pair(pair, mode.value, claim, blocked_users)
                except Exception:
                    logger.exception("matchmaking.claim_release_failed", extra={"mode": mode.value})
                logger.exception("matchmaking.game_creation_failed", extra={"mode": mode.value})
                raise HTTPException(status_code=503, detail="Match creation failed; players were returned to the queue") from exc
            game_id = recovered_id
        else:
            game_id = game.id

        player_ids = [uuid.UUID(pid) for pid in pair]
        try:
            saved = await matchmaking_service.save_pair_assignment(player_ids, game_id, mode.value, claim)
            if not saved:
                logger.warning("matchmaking.assignment_claim_expired", extra={"game_id": str(game_id)})
        except Exception:
            # The committed game is authoritative; status recovers its ID from
            # the database while the bounded Redis claim lease expires.
            logger.exception("matchmaking.assignment_save_failed", extra={"game_id": str(game_id)})
    else:
        await session.commit()

    return await _matchmaking_status(user, session, mode)


async def _matchmaking_status(user: User, session: AsyncSession, mode: MatchmakingMode):
    user_id = uuid.UUID(user.id)
    game_id = await _active_match_id(session, user_id, mode.value)
    if game_id:
        return {"status": "matched", "game_id": str(game_id)}
    await _clear_finished_assignment(session, user_id)
    state = await matchmaking_service.matchmaking_status(user_id, mode)
    return {"status": state}


def _config_for_mode(mode: MatchmakingMode) -> Optional[GameConfig]:
    settings = get_settings()
    if mode == MatchmakingMode.MINI:
        return GameConfig(
            board_width=settings.mini_board_width,
            board_height=settings.mini_board_height,
            initial_time_ms=settings.mini_game_clock_ms,
        )
    return None


async def _ranked_matchmaking_status(user: User, session: AsyncSession):
    user_id = uuid.UUID(user.id)
    game_id = await _active_match_id(session, user_id, "ranked")
    if game_id:
        return {"status": "matched", "game_id": str(game_id)}
    await _clear_finished_assignment(session, user_id)
    state = await matchmaking_service.ranked_queue_status(user_id)
    if state == "matched":
        game_id = await matchmaking_service.get_assignment(user_id, "ranked")
        if game_id:
            return {"status": "matched", "game_id": game_id}
    return {"status": state}


async def _active_match_id(session: AsyncSession, user_id: uuid.UUID, mode: str) -> uuid.UUID | None:
    repo = GameRepository(session)
    game = await repo.find_active_match_for_user(user_id, mode)
    if game:
        return game.id

    legacy_match = await _legacy_assigned_match(session, user_id)
    if legacy_match and legacy_match[1] == mode:
        return legacy_match[0].id
    return None


async def _active_pair_users(pair: tuple[str, str]) -> set[str]:
    """Do not put a player back into a queue if another request already matched them."""
    blocked: set[str] = set()
    try:
        async with Database.session_factory()() as session:
            repo = GameRepository(session)
            for player_id in pair:
                user_id = uuid.UUID(player_id)
                if await repo.find_active_match_for_user(user_id):
                    blocked.add(player_id)
    except Exception:
        logger.exception("matchmaking.active_pair_lookup_failed")
    return blocked


async def _clear_finished_assignment(session: AsyncSession, user_id: uuid.UUID) -> None:
    value = await matchmaking_service.get_assignment_value(user_id)
    if not value or "|" not in value:
        return
    try:
        game_id = uuid.UUID(value.split("|", 1)[1])
        game = await GameRepository(session).get(game_id)
    except ValueError:
        game = None
    if (
        game
        and game.status.value in {"READY", "ACTIVE"}
        and user_id in {game.player_1_id, game.player_2_id}
    ):
        return
    await matchmaking_service.clear_assignment_if_value(user_id, value)


async def _legacy_assigned_match(
    session: AsyncSession, user_id: uuid.UUID
) -> tuple[db.GameModel, str | None] | None:
    """Resolve the pre-contract Redis assignment value while old entries expire."""
    legacy_assignment = await matchmaking_service.get_assignment(user_id)
    if legacy_assignment:
        try:
            candidate = await GameRepository(session).get(uuid.UUID(legacy_assignment))
        except ValueError:
            candidate = None
        if (
            candidate
            and candidate.status.value in {"READY", "ACTIVE"}
            and user_id in {candidate.player_1_id, candidate.player_2_id}
        ):
            candidate_mode = candidate.matchmaking_mode
            if candidate_mode is None:
                candidate_mode = "ranked" if candidate.is_ranked else _infer_legacy_mode(candidate)
            return candidate, candidate_mode
    return None


def _infer_legacy_mode(game: db.GameModel) -> str | None:
    settings = get_settings()
    if (game.board_width, game.board_height) == (settings.mini_board_width, settings.mini_board_height):
        return MatchmakingMode.MINI.value
    if (game.board_width, game.board_height) == (settings.board_width, settings.board_height):
        return MatchmakingMode.STANDARD.value
    return None


async def _create_match_for_pair(
    session: AsyncSession,
    pair: tuple[str, str],
    config: Optional[GameConfig] = None,
    *,
    is_ranked: bool = False,
    matchmaking_mode: str | None = None,
):
    repo = UserRepository(session)
    pair_ids = tuple(sorted((uuid.UUID(pair[0]), uuid.UUID(pair[1]))))
    locked_users = await repo.lock_many(pair_ids)
    if len(locked_users) != 2:
        raise HTTPException(status_code=404, detail="A matched user no longer exists")

    # Redis is the fast claim authority; locking the user rows and checking the
    # database here is the durable guard against duplicate active games across
    # Redis expiry, process restarts, and overlapping queue modes.
    active_game = await GameRepository(session).find_active_match_for_users(pair_ids)
    if active_game:
        raise HTTPException(status_code=409, detail="A matched user already has an active matchmaking game")

    player_models = [locked_users[uuid.UUID(player_id)] for player_id in pair]

    auth_players = [auth_service.model_to_user(model) for model in player_models]
    game = await game_service.create_game(
        session,
        auth_players[0],
        config=config,
        is_ranked=is_ranked,
        matchmaking_mode=matchmaking_mode,
    )
    await game_service.join_game(session, game.id, auth_players[1], preferred_slot=1)
    return game
