from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
from fastapi import HTTPException

from app.api import games as games_api
from app.auth.models import User
from app.games.events import GameEvent
from app.games.exceptions import ReactionCycleError
from app.games.models import GameStatus
from app.games.schemas import MoveRequest


def make_user() -> User:
    now = datetime.now(timezone.utc)
    return User(id=str(uuid.uuid4()), username="player", created_at=now, updated_at=now)


@pytest.mark.asyncio
async def test_submit_move_commits_successful_transaction(monkeypatch):
    game_id = uuid.uuid4()
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    event = GameEvent(
        sequence=1,
        event_type="move",
        payload={
            "turn_number": 1,
            "player_slot": 0,
            "reaction": {},
            "final_state": {"turn_number": 1},
            "status": GameStatus.ACTIVE.value,
        },
    )
    monkeypatch.setattr(games_api.game_service, "submit_move", AsyncMock(return_value=event))

    response = await games_api.submit_move(
        game_id=game_id,
        body=MoveRequest(cell=0, client_move_id="move-1"),
        user=make_user(),
        session=session,
    )

    assert response.sequence == 1
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_move_rolls_back_and_hides_internal_errors(monkeypatch):
    game_id = uuid.uuid4()
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    monkeypatch.setattr(
        games_api.game_service,
        "submit_move",
        AsyncMock(side_effect=RuntimeError("sensitive database details")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await games_api.submit_move(
            game_id=game_id,
            body=MoveRequest(cell=0, client_move_id="move-2"),
            user=make_user(),
            session=session,
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Move could not be processed"
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_move_rolls_back_controlled_reaction_error(monkeypatch):
    game_id = uuid.uuid4()
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    monkeypatch.setattr(
        games_api.game_service,
        "submit_move",
        AsyncMock(side_effect=ReactionCycleError()),
    )

    with pytest.raises(ReactionCycleError) as exc_info:
        await games_api.submit_move(
            game_id=game_id,
            body=MoveRequest(cell=0, client_move_id="move-cycle"),
            user=make_user(),
            session=session,
        )

    assert exc_info.value.status_code == 422
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
