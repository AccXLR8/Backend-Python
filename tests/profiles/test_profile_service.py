from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import models as db
from app.profiles.service import profile_service


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(db.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            db.AvatarModel(
                id=1,
                code="green-reactor",
                name="Green Reactor",
                image_url="/avatars/green-reactor.svg",
                enabled=True,
                sort_order=1,
            )
        )
        session.add(
            db.AchievementModel(
                id=1,
                code="WIN_STREAK_20",
                name="Unstoppable",
                description="Win 20 consecutive matches.",
                badge_image_url="/badges/unstoppable.svg",
                enabled=True,
            )
        )
        await session.commit()
        yield session
    await engine.dispose()


async def create_player(session, username: str):
    now = datetime.now(UTC)
    user = db.UserModel(
        id=uuid.uuid4(),
        username=username,
        password_hash=None,
        is_guest=False,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    profile_service.create_records_for_new_user(session, user.id, created_at=now)
    await session.commit()
    return user


@pytest.mark.asyncio
async def test_profile_response_and_longest_chain(session):
    user = await create_player(session, "PlayerOne")

    await profile_service.record_longest_chain(session, user.id, 7)
    await profile_service.record_longest_chain(session, user.id, 3)
    await session.commit()

    profile = await profile_service.get_profile_by_user_id(session, user.id)
    assert profile.username == "PlayerOne"
    assert profile.avatar.code == "green-reactor"
    assert profile.stats.longest_chain == 7
    assert profile.stats.matches_played == 0
    assert profile.stats.win_rate == 0


@pytest.mark.asyncio
async def test_profile_update_enforces_case_insensitive_unique_username_and_avatar_catalog(session):
    first = await create_player(session, "FirstPlayer")
    first_id = first.id
    await create_player(session, "TakenName")

    with pytest.raises(HTTPException) as duplicate:
        await profile_service.update_profile(
            session,
            first_id,
            username="takenname",
            avatar_id=None,
        )
    assert duplicate.value.status_code == 409
    await session.rollback()

    with pytest.raises(HTTPException) as invalid_avatar:
        await profile_service.update_profile(
            session,
            first_id,
            username=None,
            avatar_id=999,
        )
    assert invalid_avatar.value.status_code == 422
    await session.rollback()

    updated = await profile_service.update_profile(
        session,
        first_id,
        username="FIRSTPLAYER",
        avatar_id=None,
    )
    assert updated.username == "FIRSTPLAYER"


@pytest.mark.asyncio
async def test_finished_games_increment_stats_once_and_award_streak_badge(session):
    winner = await create_player(session, "Winner")
    loser = await create_player(session, "Loser")

    for index in range(20):
        game = db.GameModel(
            id=uuid.uuid4(),
            status=db.GameStatus.FINISHED,
            player_1_id=winner.id,
            player_2_id=loser.id,
            winner_id=winner.id,
            loser_id=loser.id,
            board_width=5,
            board_height=5,
            initial_clock_ms=600_000,
            turn_number=index + 1,
        )
        participants = [
            db.GameParticipantModel(
                game_id=game.id,
                user_id=winner.id,
                player_slot=0,
                remaining_time_ms=500_000,
            ),
            db.GameParticipantModel(
                game_id=game.id,
                user_id=loser.id,
                player_slot=1,
                remaining_time_ms=500_000,
            ),
        ]
        session.add(game)
        session.add_all(participants)
        await session.flush()

        assert await profile_service.apply_finished_game(session, game, participants) is True
        assert await profile_service.apply_finished_game(session, game, participants) is False
        await session.commit()

    winner_stats = await session.get(db.PlayerStatsModel, winner.id)
    loser_stats = await session.get(db.PlayerStatsModel, loser.id)
    assert winner_stats.matches_played == 20
    assert winner_stats.matches_won == 20
    assert winner_stats.current_win_streak == 20
    assert winner_stats.longest_win_streak == 20
    assert loser_stats.matches_played == 20
    assert loser_stats.matches_won == 0
    assert loser_stats.current_win_streak == 0

    awards = (
        await session.execute(
            select(db.UserAchievementModel).where(
                db.UserAchievementModel.user_id == winner.id
            )
        )
    ).scalars().all()
    assert len(awards) == 1
    assert awards[0].achievement_id == 1


@pytest.mark.asyncio
async def test_loss_resets_existing_win_streak(session):
    first = await create_player(session, "First")
    second = await create_player(session, "Second")
    first_stats = await session.get(db.PlayerStatsModel, first.id)
    first_stats.current_win_streak = 8
    first_stats.longest_win_streak = 8
    await session.commit()

    game = db.GameModel(
        id=uuid.uuid4(),
        status=db.GameStatus.FINISHED,
        player_1_id=first.id,
        player_2_id=second.id,
        winner_id=second.id,
        loser_id=first.id,
    )
    participants = [
        db.GameParticipantModel(game_id=game.id, user_id=first.id, player_slot=0, remaining_time_ms=1),
        db.GameParticipantModel(game_id=game.id, user_id=second.id, player_slot=1, remaining_time_ms=1),
    ]
    session.add(game)
    session.add_all(participants)
    await session.flush()
    await profile_service.apply_finished_game(session, game, participants)
    await session.commit()

    await session.refresh(first_stats)
    assert first_stats.matches_played == 1
    assert first_stats.matches_won == 0
    assert first_stats.current_win_streak == 0
    assert first_stats.longest_win_streak == 8
