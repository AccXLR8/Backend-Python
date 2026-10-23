from __future__ import annotations

import asyncio
import json
import logging
import resource
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime
from importlib import import_module
from types import ModuleType
from typing import Any, Dict, Optional

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
from app.games.models import FinishReason, GameConfig, GameStatus
from app.utils.time import MonotonicClock

logger = logging.getLogger(__name__)


class GameService:
    def __init__(self, engine_module: Optional[ModuleType] = None):
        settings = get_settings()
        module_name = settings.engine_module
        module = engine_module or import_module(module_name)
        move_cls = getattr(module, "Move", None)
        game_state_cls = getattr(module, "GameState", None)
        illegal_cls = getattr(module, "IllegalMoveError", None)
        if illegal_cls is None:
            errors_mod = getattr(module, "errors", None)
            if errors_mod is not None:
                illegal_cls = getattr(errors_mod, "IllegalMoveError", None)
        if move_cls is None or game_state_cls is None:
            raise RuntimeError("chainreaction_py module is missing required exports")
        self.engine_module = module
        self.move_cls = move_cls
        self.game_state_cls = game_state_cls
        self.illegal_move_cls = illegal_cls
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

        engine_state_payload = await asyncio.to_thread(
            self._create_new_game_state_payload,
            config.board_width,
            config.board_height,
            [0, 1],
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
            latest_state=engine_state_payload,
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
            timestamp = datetime.utcnow()
            game.started_at = timestamp
            game.active_clock_started_at = timestamp

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
        move_event: GameEvent | None = None
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
            participant_lookup: Dict[int, db.GameParticipantModel] = {p.player_slot: p for p in participants}
            now_wall = datetime.utcnow()
            self._apply_elapsed_time(game, participant_lookup, now_wall)

            current_clock = participant_lookup.get(player_slot)
            if current_clock is None:
                raise PlayerNotInGameError()

            if current_clock.remaining_time_ms <= 0:
                move_event = await self._handle_timeout(
                    game=game,
                    participant_lookup=participant_lookup,
                    timed_out_slot=player_slot,
                    move_repo=move_repo,
                    event_repo=event_repo,
                )
                game_finished = True
            else:
                move_start = self.monotonic_clock.now()
                row = cell // game.board_width
                col = cell % game.board_width

                try:
                    (
                        final_state_payload,
                        reaction_payload,
                        outcome_payload,
                        payload_bytes,
                        timeline_steps,
                    ) = await asyncio.to_thread(
                        self._apply_move_payloads,
                        game.latest_state or {"board": []},
                        player_slot,
                        row,
                        col,
                    )
                except Exception as exc:  # pragma: no cover - depends on engine runtime
                    if self.illegal_move_cls and isinstance(exc, self.illegal_move_cls):
                        raise IllegalMoveError(str(exc)) from exc
                    raise

                processing_elapsed_ms = int((self.monotonic_clock.now() - move_start) * 1000)
                if processing_elapsed_ms > 0:
                    current_clock.remaining_time_ms = max(0, current_clock.remaining_time_ms - processing_elapsed_ms)

                next_player = final_state_payload.get("current_player")
                if next_player is None:
                    next_player = 1 - player_slot

                final_state_size = len(json.dumps(final_state_payload)) if final_state_payload else 0
                reaction_size = len(json.dumps(reaction_payload)) if reaction_payload else 0
                latest_state_size = len(json.dumps(game.latest_state or {}))

                game.turn_number = int(final_state_payload.get("turn_number", game.turn_number + 1))
                game.current_player_slot = next_player
                game.latest_state = final_state_payload
                game.status = GameStatus.ACTIVE

                outcome = outcome_payload or {}
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

                if game.status == GameStatus.FINISHED:
                    game.current_player_slot = None
                    game.active_clock_started_at = None
                else:
                    game.active_clock_started_at = datetime.utcnow()

                sequence = await move_repo.last_sequence(game_id) + 1
                move = db.MoveModel(
                    game_id=game_id,
                    player_slot=player_slot,
                    cell=cell,
                    sequence=sequence,
                    turn_number=game.turn_number,
                    client_move_id=client_move_id,
                    time_remaining_ms=current_clock.remaining_time_ms,
                )
                await move_repo.create(move)

                event_payload = {
                    "player_slot": player_slot,
                    "cell": cell,
                    "reaction": reaction_payload,
                    "final_state": final_state_payload,
                    "outcome": outcome,
                    "turn_number": game.turn_number,
                    "status": game.status.value,
                    "time_remaining_ms": current_clock.remaining_time_ms,
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
                        "engine_payload_bytes": payload_bytes,
                        "timeline_steps": timeline_steps,
                    },
                )

                move_event = GameEvent(sequence=sequence, event_type="move", payload=event_payload)

        if move_event is None:  # pragma: no cover - defensive guard
            raise RuntimeError("Move event was not generated")

        if game_finished:
            game_lock_manager.release(str(game_id))

        return move_event

    def _player_slot_for_user(self, game: db.GameModel, user_id: uuid.UUID) -> int:
        if game.player_1_id == user_id:
            return 0
        if game.player_2_id == user_id:
            return 1
        raise PlayerNotInGameError()

    def _apply_elapsed_time(
        self,
        game: db.GameModel,
        participant_lookup: Dict[int, db.GameParticipantModel],
        now: datetime,
    ) -> None:
        slot = game.current_player_slot
        if slot is None:
            return
        started_at = game.active_clock_started_at
        if started_at is None:
            game.active_clock_started_at = now
            return
        elapsed_seconds = max(0.0, (now - started_at).total_seconds())
        elapsed_ms = int(elapsed_seconds * 1000)
        if elapsed_ms == 0:
            return
        clock = participant_lookup.get(slot)
        if not clock:
            return
        clock.remaining_time_ms = max(0, clock.remaining_time_ms - elapsed_ms)
        game.active_clock_started_at = now

    async def _handle_timeout(
        self,
        game: db.GameModel,
        participant_lookup: Dict[int, db.GameParticipantModel],
        timed_out_slot: int,
        move_repo: MoveRepository,
        event_repo: GameEventRepository,
    ) -> GameEvent:
        winner_slot = 1 - timed_out_slot
        now = datetime.utcnow()
        game.status = GameStatus.FINISHED
        game.finished_at = now
        game.finish_reason = FinishReason.TIMEOUT.value
        game.current_player_slot = None
        game.active_clock_started_at = None
        timed_out_clock = participant_lookup.get(timed_out_slot)
        if timed_out_clock:
            timed_out_clock.remaining_time_ms = 0
        if winner_slot == 0:
            game.winner_id = game.player_1_id
            game.loser_id = game.player_2_id
        elif winner_slot == 1:
            game.winner_id = game.player_2_id
            game.loser_id = game.player_1_id

        sequence = await move_repo.last_sequence(game.id) + 1
        payload = {
            "player_slot": timed_out_slot,
            "reaction": {},
            "final_state": game.latest_state,
            "outcome": {
                "status": {"kind": "won"},
                "winner": winner_slot,
                "reason": FinishReason.TIMEOUT.value,
            },
            "turn_number": game.turn_number,
            "status": game.status.value,
            "finish_reason": FinishReason.TIMEOUT.value,
            "time_remaining_ms": timed_out_clock.remaining_time_ms if timed_out_clock else 0,
        }
        event = db.GameEventModel(
            game_id=game.id,
            sequence=sequence,
            event_type="timeout",
            payload=payload,
        )
        await event_repo.append(event)
        logger.info(
            "game.timeout",
            extra={
                "game_id": str(game.id),
                "timed_out_slot": timed_out_slot,
                "winner_slot": winner_slot,
            },
        )
        return GameEvent(sequence=sequence, event_type="timeout", payload=payload)

    def _create_new_game_state_payload(self, width: int, height: int, players: list[int]) -> dict[str, Any]:
        state = self.engine_module.new_game(width=width, height=height, players=list(players))
        payload = self._game_state_to_payload(state)
        logger.info(
            "engine.new_game_state",
            extra={
                "width": width,
                "height": height,
                "players": list(players),
                "payload_bytes": len(json.dumps(payload)),
            },
        )
        return payload

    def _apply_move_payloads(
        self,
        state_payload: dict[str, Any],
        player_slot: int,
        row: int,
        col: int,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], int, int]:
        cr_state = self._game_state_from_payload(state_payload)
        move = self.move_cls(row=row, col=col)
        result = self.engine_module.apply_move(cr_state, player=player_slot, mv=move)
        final_state = self._game_state_to_payload(result.final_state)
        reaction = self._to_plain(getattr(result, "timeline", None)) or {}
        outcome = self._to_plain(getattr(result, "outcome", None)) or {}
        payload_bytes = len(json.dumps({"final_state": final_state, "timeline": reaction, "outcome": outcome}))
        timeline_steps = 0
        if isinstance(reaction, dict):
            steps = reaction.get("steps")
            if isinstance(steps, list):
                timeline_steps = len(steps)
        logger.info(
            "engine.apply_move",
            extra={
                "player_slot": player_slot,
                "move_row": row,
                "move_col": col,
                "payload_bytes": payload_bytes,
                "timeline_steps": timeline_steps,
            },
        )
        return final_state, reaction, outcome, payload_bytes, timeline_steps

    def _game_state_to_payload(self, state: Any) -> dict[str, Any]:
        if state is None:
            raise ValueError("Engine returned an empty state")
        if hasattr(state, "to_dict"):
            return state.to_dict()
        if hasattr(state, "to_json"):
            return json.loads(state.to_json())
        if is_dataclass(state):
            return asdict(state)
        if isinstance(state, dict):
            return state
        raise TypeError(f"Cannot serialize engine state of type {type(state)!r}")

    def _game_state_from_payload(self, payload: dict[str, Any]):
        game_state_cls = self.game_state_cls
        if hasattr(game_state_cls, "from_dict"):
            return game_state_cls.from_dict(payload)
        if hasattr(game_state_cls, "from_json"):
            return game_state_cls.from_json(json.dumps(payload))
        return game_state_cls(**payload)

    def _to_plain(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {k: self._to_plain(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._to_plain(v) for v in value]
        if hasattr(value, "to_dict"):
            return value.to_dict()
        if hasattr(value, "model_dump"):
            return value.model_dump()
        if is_dataclass(value):
            return asdict(value)
        return value


def _build_game_service() -> GameService:
    settings = get_settings()
    module = import_module(settings.engine_module)
    return GameService(engine_module=module)


game_service = _build_game_service()
