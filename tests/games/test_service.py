from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
import game_engine

from app.auth.models import User
from app.database.repositories import GameEventRepository, GameRepository, MoveRepository
from app.games.exceptions import (
    IllegalMoveError,
    ReactionCycleError,
    ReactionLimitExceededError,
    ReactionProcessingError,
)
from app.games.models import GameStatus
from app.games.service import GameService


def build_service() -> GameService:
    return GameService(engine_api=game_engine)


def test_engine_state_round_trip_and_move_application():
    service = build_service()
    state = service._create_new_game_state_payload(5, 5, [0, 1])

    final_state, timeline, outcome, payload_bytes, timeline_steps = service._apply_move_payloads(
        state, player_slot=0, row=0, col=0
    )

    assert final_state["board"]["cells"][0][0] == {"owner": 0, "orb_count": 1}
    assert final_state["turn_number"] == 1
    assert final_state["current_player_index"] == 1
    assert timeline["placement"] == {"cell": 0, "player": 0}
    assert outcome["status"]["kind"] == "active"
    assert payload_bytes > 0
    assert timeline_steps == 0


def test_game_engine_illegal_move_type_is_detected():
    service = build_service()
    assert service.illegal_move_cls is game_engine.IllegalMove


def test_new_reaction_errors_map_to_controlled_http_errors():
    class ReactionError(Exception):
        pass

    class ReactionCycle(ReactionError):
        pass

    class ReactionLimitExceeded(ReactionError):
        pass

    service = build_service()
    service.reaction_error_cls = ReactionError
    service.reaction_cycle_cls = ReactionCycle
    service.reaction_limit_cls = ReactionLimitExceeded

    assert isinstance(service._map_engine_error(ReactionCycle()), ReactionCycleError)
    assert isinstance(service._map_engine_error(ReactionLimitExceeded()), ReactionLimitExceededError)
    assert isinstance(service._map_engine_error(ReactionError()), ReactionProcessingError)
    assert isinstance(service._map_engine_error(game_engine.IllegalMove.out_of_bounds()), IllegalMoveError)


def test_reaction_error_does_not_mutate_stored_state(monkeypatch):
    class ReactionCycle(Exception):
        pass

    service = build_service()
    state = service._create_new_game_state_payload(5, 5, [0, 1])
    original_state = service._to_plain(state)
    monkeypatch.setattr(service.engine, "apply_move", lambda *_args, **_kwargs: (_ for _ in ()).throw(ReactionCycle()))

    with pytest.raises(ReactionCycle):
        service._apply_move_payloads(state, player_slot=0, row=0, col=0)

    assert state == original_state


def test_elapsed_clock_handles_timezone_aware_database_timestamp():
    service = build_service()
    now = datetime.now(timezone.utc)
    game = SimpleNamespace(current_player_slot=0, active_clock_started_at=now - timedelta(seconds=2))
    participant = SimpleNamespace(remaining_time_ms=10_000)

    service._apply_elapsed_time(game, {0: participant}, now)

    assert 7_900 <= participant.remaining_time_ms <= 8_100
    assert game.active_clock_started_at == now


def test_elapsed_clock_treats_legacy_naive_timestamp_as_utc():
    service = build_service()
    aware_now = datetime.now(timezone.utc)
    naive_started = (aware_now - timedelta(seconds=1)).replace(tzinfo=None)
    game = SimpleNamespace(current_player_slot=0, active_clock_started_at=naive_started)
    participant = SimpleNamespace(remaining_time_ms=10_000)

    service._apply_elapsed_time(game, {0: participant}, aware_now)

    assert 8_900 <= participant.remaining_time_ms <= 9_100


@pytest.mark.asyncio
async def test_submit_move_uses_combined_load_and_defers_inserts(monkeypatch):
    service = build_service()
    state = service._create_new_game_state_payload(5, 5, [0, 1])
    player_1_id = uuid.uuid4()
    player_2_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    game = SimpleNamespace(
        id=uuid.uuid4(),
        status=GameStatus.READY,
        player_1_id=player_1_id,
        player_2_id=player_2_id,
        current_player_slot=0,
        active_clock_started_at=now,
        board_width=5,
        board_height=5,
        latest_state=state,
        turn_number=0,
        winner_id=None,
        loser_id=None,
        finished_at=None,
        finish_reason=None,
    )
    participants = [
        SimpleNamespace(player_slot=0, remaining_time_ms=600_000),
        SimpleNamespace(player_slot=1, remaining_time_ms=600_000),
    ]
    combined_load = AsyncMock(return_value=(game, participants))
    duplicate_lookup = AsyncMock(return_value=None)
    create_move = AsyncMock(side_effect=lambda move, **_: move)
    append_event = AsyncMock(side_effect=lambda event, **_: event)
    last_sequence = AsyncMock(return_value=99)
    monkeypatch.setattr(GameRepository, "get_with_participants_for_update", combined_load)
    monkeypatch.setattr(MoveRepository, "by_client_move_id", duplicate_lookup)
    monkeypatch.setattr(MoveRepository, "create", create_move)
    monkeypatch.setattr(MoveRepository, "last_sequence", last_sequence)
    monkeypatch.setattr(GameEventRepository, "append", append_event)

    user = User(
        id=str(player_1_id),
        username="p1",
        created_at=now,
        updated_at=now,
    )
    event = await service.submit_move(
        session=SimpleNamespace(),
        game_id=game.id,
        player=user,
        cell=0,
        client_move_id="client-move-1",
    )

    assert event.sequence == 1
    combined_load.assert_awaited_once_with(game.id)
    duplicate_lookup.assert_awaited_once_with(game.id, "client-move-1")
    last_sequence.assert_not_awaited()
    assert create_move.await_args.kwargs["flush"] is False
    assert append_event.await_args.kwargs["flush"] is False
    assert event.payload["current_player_slot"] == 1
    assert event.payload["clocks"]["0"] <= 600_000
