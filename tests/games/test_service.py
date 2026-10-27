from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import python_engine

from app.games.service import GameService


def build_service() -> GameService:
    return GameService(engine_module=python_engine)


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


def test_python_engine_illegal_move_type_is_detected():
    service = build_service()
    assert service.illegal_move_cls is python_engine.IllegalMove


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
