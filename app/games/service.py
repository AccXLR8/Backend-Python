from __future__ import annotations

import json
import logging
import resource
import uuid
from datetime import datetime
from importlib import import_module
from typing import Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.config.settings import get_settings
from app.database import models as db
from app.database.repositories import (
    GameEventRepository,
    GameRepository,
    MoveRepository,
    ParticipantRepository,
    UserRepository,
)
from app.engine.adapter import (
    ChainReactionPyEngineAdapter,
    EngineAdapter,
    EngineIllegalMoveError,
    FakeEngineAdapter,
)
from app.engine.models import EngineGameState, EngineMove
from app.games.clock import GameClockState
from app.games.events import GameEvent
from app.games.exceptions import (
    GameFinishedError,
    GameFullError,
    GameNotFoundError,
    IllegalMoveError,
    MoveAlreadyProcessedError,
    NotYourTurnError,
    PlayerNotInGameError,
)
from app.games.manager import game_lock_manager
from app.games.models import FinishReason, GameConfig, GameStatus, PlayerClock
from app.utils.time import MonotonicClock

logger = logging.getLogger(__name__)


class GameService:
    def __init__(self, engine_adapter: Optional[EngineAdapter] = None):
        settings = get_settings()
        self.engine_adapter = engine_adapter or ChainReactionPyEngineAdapter(settings.engine_module)
        self.monotonic_clock = MonotonicClock()
        self.settings = settings

    async def create_game(self, session: AsyncSession, creator: User, config: Optional[GameConfig] = None) -> db.GameModel:
        config = config or GameConfig(
            board_width=self.settings.board_width,
            board_height=self.settings.board_height,
            initial_time_ms=self.settings.game_clock_ms,
        )
        user_repo = UserRepository(session)
        creator_id = uuid.UUID(creator.id)
        await user_repo.get_or_create(creator_id, creator.username)

        engine_state = await self.engine_adapter.new_game_state(
            width=config.board_width,
            height=config.board_height,
            players=[0, 1],
        )

        game_repo = GameRepository(session)
        participant_repo = ParticipantRepository(session)
        game = db.GameModel(
            status=GameStatus.WAITING,
            board_width=config.board_width,
            board_height=config.board_height,
            initial_clock_ms=config.initial_time_ms,
            current_player_slot=0,
            player_1_id=creator_id,
            latest_state=engine_state.data,
            turn_number=0,
        )
        await game_repo.create(game)
        participant = db.GameParticipantModel(
            game_id=game.id,
            user_id=creator_id,
            player_slot=0,
            remaining_time_ms=config.initial_time_ms,
        )
        await participant_repo.create(participant)
        return game

    async def join_game(
        self,
        session: AsyncSession,
        game_id: uuid.UUID,
        user: User,
        preferred_slot: Optional[int] = None,
    ) -> db.GameModel:
        repo = GameRepository(session)
        participant_repo = ParticipantRepository(session)
        user_repo = UserRepository(session)

        game = await repo.get(game_id)
        if not game:
            raise GameNotFoundError()

        user_id = uuid.UUID(user.id)
        await user_repo.get_or_create(user_id, user.username)

        existing = await participant_repo.get(game_id, user_id)
        if existing:
            return game

        slot: Optional[int] = None
        if preferred_slot in {0, 1}:
            if preferred_slot == 0 and (game.player_1_id is None or game.player_1_id == user_id):
                slot = 0
            if preferred_slot == 1 and (game.player_2_id is None or game.player_2_id == user_id):
                slot = 1

        if slot is None:
            if game.player_1_id is None:
                slot = 0
                game.player_1_id = user_id
            elif game.player_2_id is None:
                slot = 1
                game.player_2_id = user_id
        else:
            if slot == 0:
                game.player_1_id = user_id
            else:
                game.player_2_id = user_id

        if slot is None:
            if game.player_1_id == user_id:
                slot = 0
            elif game.player_2_id == user_id:
                slot = 1
            else:
                raise GameFullError()

        participant = db.GameParticipantModel(
            game_id=game.id,
            user_id=user_id,
            player_slot=slot,
            remaining_time_ms=game.initial_clock_ms,
        )
        await participant_repo.create(participant)

        if game.player_1_id and game.player_2_id:
            game.status = GameStatus.READY
            game.current_player_slot = 0
            game.started_at = datetime.utcnow()

        return game

    async def submit_move(
        self,
        session: AsyncSession,
        game_id: uuid.UUID,
        player: User,
        cell: int,
        client_move_id: Optional[str] = None,
    ) -> GameEvent:
        repo = GameRepository(session)
        participant_repo = ParticipantRepository(session)
        move_repo = MoveRepository(session)
        event_repo = GameEventRepository(session)

        game = await repo.get(game_id)
        if not game:
            raise GameNotFoundError()
        if game.status not in {GameStatus.ACTIVE, GameStatus.READY}:
            raise GameFinishedError()
        if not (game.player_1_id and game.player_2_id):
            raise GameFinishedError()

        player_id = uuid.UUID(player.id)
        player_slot = self._player_slot_for_user(game, player_id)

        lock = game_lock_manager.get_lock(str(game_id))
        game_finished = False
        async with lock:
            if game.current_player_slot is None:
                game.current_player_slot = 0

            if player_slot != game.current_player_slot:
                raise NotYourTurnError()

            if client_move_id:
                existing_move = await move_repo.by_client_move_id(game_id, client_move_id)
                if existing_move:
                    raise MoveAlreadyProcessedError()

            participants = await participant_repo.list_for_game(game_id)
            clock_state = self._build_clock_state(participants, game.current_player_slot)
            now = self.monotonic_clock.now()
            clock_state.start(player_slot, now)
            if not clock_state.has_time(player_slot, now):
                raise GameFinishedError()

            engine_state = EngineGameState(game.latest_state or {"board": []})
            engine_move = EngineMove(row=cell // game.board_width, col=cell % game.board_width)

            try:
                result = await self.engine_adapter.apply_move(engine_state, player_slot, engine_move)
            except EngineIllegalMoveError as exc:  # pragma: no cover - depends on engine runtime
                raise IllegalMoveError(str(exc)) from exc

            clock_state.stop(player_slot, now)
            next_player = (
                result.final_state.data.get("current_player")
                if isinstance(result.final_state.data, dict)
                else None
            )
            if next_player is None:
                next_player = 1 - player_slot
            clock_state.start(next_player, now)

            final_state_payload = result.final_state.data
            final_state_size = len(json.dumps(final_state_payload)) if final_state_payload else 0
            reaction_payload = result.reaction or {}
            reaction_size = len(json.dumps(reaction_payload)) if reaction_payload else 0
            latest_state_size = len(json.dumps(game.latest_state or {}))

            game.turn_number = int(final_state_payload.get("turn_number", game.turn_number + 1))
            game.current_player_slot = next_player
            game.latest_state = final_state_payload
            game.status = GameStatus.ACTIVE

            outcome = result.result or {}
            status_meta = outcome.get("status") or {}
            if isinstance(status_meta, dict):
                status_kind = status_meta.get("kind")
            elif isinstance(status_meta, str):
                status_kind = status_meta
            else:
                status_kind = None
            if status_kind and status_kind.lower() == "won":
                game.status = GameStatus.FINISHED
                game.finished_at = datetime.utcnow()
                game.finish_reason = FinishReason.NORMAL.value
                winner_slot = outcome.get("winner")
                if winner_slot == 0:
                    game.winner_id = game.player_1_id
                    game.loser_id = game.player_2_id
                elif winner_slot == 1:
                    game.winner_id = game.player_2_id
                    game.loser_id = game.player_1_id
                game_finished = True

            participant_lookup: Dict[int, db.GameParticipantModel] = {p.player_slot: p for p in participants}
            if player_slot in participant_lookup:
                participant_lookup[player_slot].remaining_time_ms = clock_state.clocks[player_slot].remaining_ms
            if next_player in participant_lookup:
                participant_lookup[next_player].remaining_time_ms = clock_state.clocks[next_player].remaining_ms

            sequence = await move_repo.last_sequence(game_id) + 1
            move = db.MoveModel(
                game_id=game_id,
                player_slot=player_slot,
                cell=cell,
                sequence=sequence,
                turn_number=game.turn_number,
                client_move_id=client_move_id,
                time_remaining_ms=clock_state.clocks[player_slot].remaining_ms,
            )
            await move_repo.create(move)

            event_payload = {
                "player_slot": player_slot,
                "cell": cell,
                "reaction": result.reaction,
                "final_state": result.final_state.data,
                "outcome": outcome,
                "turn_number": game.turn_number,
                "status": game.status.value,
            }
            event = db.GameEventModel(
                game_id=game_id,
                sequence=sequence,
                event_type="move",
                payload=event_payload,
            )
            await event_repo.append(event)

            rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            logger.info(
                "move.processed",
                extra={
                    "game_id": str(game_id),
                    "sequence": sequence,
                    "player_slot": player_slot,
                    "cell": cell,
                    "final_state_bytes": final_state_size,
                    "reaction_bytes": reaction_size,
                    "latest_state_bytes": latest_state_size,
                    "rss_kb": rss_kb,
                    "status": game.status.value,
                },
            )

            move_event = GameEvent(sequence=sequence, event_type="move", payload=event_payload)

        if game_finished:
            game_lock_manager.release(str(game_id))

        return move_event

    def _player_slot_for_user(self, game: db.GameModel, user_id: uuid.UUID) -> int:
        if game.player_1_id == user_id:
            return 0
        if game.player_2_id == user_id:
            return 1
        raise PlayerNotInGameError()

    def _build_clock_state(self, participants, active_slot: Optional[int]) -> GameClockState:
        clocks = {}
        for participant in participants:
            clocks[participant.player_slot] = PlayerClock(
                player_slot=participant.player_slot,
                remaining_ms=participant.remaining_time_ms,
            )
        if 0 not in clocks:
            clocks[0] = PlayerClock(player_slot=0, remaining_ms=self.settings.game_clock_ms)
        if 1 not in clocks:
            clocks[1] = PlayerClock(player_slot=1, remaining_ms=self.settings.game_clock_ms)
        return GameClockState(clocks=clocks, active_slot=active_slot)


def _build_game_service() -> GameService:
    settings = get_settings()
    try:
        import_module(settings.engine_module)
        adapter: EngineAdapter = ChainReactionPyEngineAdapter(settings.engine_module)
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.warning("Falling back to FakeEngineAdapter: %s", exc)
        adapter = FakeEngineAdapter()
    return GameService(engine_adapter=adapter)


game_service = _build_game_service()
