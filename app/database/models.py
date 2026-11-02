from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    JSON,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class GameStatus(str, Enum):
    WAITING = "WAITING"
    READY = "READY"
    ACTIVE = "ACTIVE"
    FINISHED = "FINISHED"
    ABANDONED = "ABANDONED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    google_subject: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    is_guest: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # ISO 3166-1 alpha-2 code, or "global" when the player has not selected one.
    nationality: Mapped[str] = mapped_column(String(8), default="global", server_default="global", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    games_as_player1 = relationship("GameModel", foreign_keys="GameModel.player_1_id")
    games_as_player2 = relationship("GameModel", foreign_keys="GameModel.player_2_id")


class AvatarModel(Base):
    __tablename__ = "avatars"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    image_url: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class UserProfileModel(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    avatar_id: Mapped[int] = mapped_column(ForeignKey("avatars.id"), nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class OrbSkinModel(Base):
    __tablename__ = "orb_skins"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(96), nullable=False)
    asset_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    preview_asset_key: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_version: Mapped[str] = mapped_column(String(64), default="1", server_default="1", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class UserOrbSkinModel(Base):
    __tablename__ = "user_orb_skins"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    skin_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("orb_skins.id"), primary_key=True
    )


class UserOrbSkinSelectionModel(Base):
    __tablename__ = "user_orb_skin_selections"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    skin_id: Mapped[str] = mapped_column(String(64), ForeignKey("orb_skins.id"), nullable=False)


class PlayerStatsModel(Base):
    __tablename__ = "player_stats"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    matches_played: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    matches_won: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_win_streak: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    longest_win_streak: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    longest_chain: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class CompetitiveRatingModel(Base):
    __tablename__ = "competitive_ratings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    wins: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    losses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    flux: Mapped[float] = mapped_column(Numeric(3, 2), default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class GameRatingChangeModel(Base):
    __tablename__ = "game_rating_changes"

    game_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("games.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    flux_before: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)
    flux_after: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)
    wins_after: Mapped[int] = mapped_column(Integer, nullable=False)
    losses_after: Mapped[int] = mapped_column(Integer, nullable=False)
    result: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class AchievementModel(Base):
    __tablename__ = "achievements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(96), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    badge_image_url: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class UserAchievementModel(Base):
    __tablename__ = "user_achievements"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    achievement_id: Mapped[int] = mapped_column(
        ForeignKey("achievements.id", ondelete="CASCADE"), primary_key=True
    )
    earned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    achievement_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class GameModel(Base):
    __tablename__ = "games"
    __table_args__ = (
        Index("ix_games_player1_matchmaking_status", "player_1_id", "matchmaking_mode", "status"),
        Index("ix_games_player2_matchmaking_status", "player_2_id", "matchmaking_mode", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[GameStatus] = mapped_column(SAEnum(GameStatus), default=GameStatus.WAITING)
    is_ranked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    matchmaking_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    player_1_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    player_2_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    winner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    loser_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    board_width: Mapped[int] = mapped_column(Integer, default=8)
    board_height: Mapped[int] = mapped_column(Integer, default=8)
    initial_clock_ms: Mapped[int] = mapped_column(Integer, default=300_000)
    turn_number: Mapped[int] = mapped_column(Integer, default=0)
    current_player_slot: Mapped[int | None] = mapped_column(Integer, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finish_reason: Mapped[str | None] = mapped_column(String(32))
    active_clock_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    turn_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    stats_applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    latest_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class GameParticipantModel(Base):
    __tablename__ = "game_participants"
    __table_args__ = (
        UniqueConstraint("game_id", "player_slot", name="uq_participant_game_slot"),
        UniqueConstraint("game_id", "user_id", name="uq_participant_game_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    game_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("games.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    player_slot: Mapped[int] = mapped_column(Integer)
    remaining_time_ms: Mapped[int] = mapped_column(Integer)
    connected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    result: Mapped[str | None] = mapped_column(String(16))
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MoveModel(Base):
    __tablename__ = "moves"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    game_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("games.id"))
    player_slot: Mapped[int] = mapped_column(Integer)
    cell: Mapped[int] = mapped_column(Integer)
    sequence: Mapped[int] = mapped_column(Integer)
    turn_number: Mapped[int] = mapped_column(Integer)
    client_move_id: Mapped[str | None] = mapped_column(String(64))
    reaction: Mapped[dict | None] = mapped_column(JSON)
    final_state: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    server_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    time_remaining_ms: Mapped[int] = mapped_column(Integer)


class GameEventModel(Base):
    __tablename__ = "game_events"
    __table_args__ = (Index("ix_game_events_game_sequence", "game_id", "sequence"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    game_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("games.id"))
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
