from __future__ import annotations

import uuid
import re
from datetime import datetime

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator

from app.auth.schemas import normalize_username


class AvatarSchema(BaseModel):
    id: int
    code: str
    name: str
    image_url: str


class PlayerStatsSchema(BaseModel):
    matches_played: int
    matches_won: int
    win_rate: float
    current_win_streak: int
    longest_win_streak: int
    longest_chain: int


class AchievementSchema(BaseModel):
    code: str
    name: str
    description: str
    badge_image_url: str
    earned_at: datetime | None = None
    unlocked: bool = False


class ProfileResponse(BaseModel):
    user_id: uuid.UUID
    username: str
    nationality: str = "global"
    avatar: AvatarSchema
    stats: PlayerStatsSchema
    achievements: list[AchievementSchema]
    created_at: datetime
    profile_age_days: int
    equipped_orb_skin_id: str = "classic"
    owned_orb_skin_ids: list[str] = Field(default_factory=lambda: ["classic"])


class ProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str | None = Field(default=None, min_length=3, max_length=24)
    avatar_id: int | None = Field(default=None, ge=1)
    equipped_orb_skin_id: str | None = Field(default=None, min_length=1, max_length=64)
    nationality: str | None = Field(default=None, min_length=2, max_length=8)

    @field_validator("nationality")
    @classmethod
    def validate_nationality(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip().upper()
        if normalized == "GLOBAL":
            return "global"
        if len(normalized) != 2 or not normalized.isalpha() or not normalized.isascii():
            raise ValueError('Nationality must be an ISO alpha-2 country code or "global"')
        return normalized

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return normalize_username(value)


class AvatarListResponse(BaseModel):
    avatars: list[AvatarSchema]


class AchievementListResponse(BaseModel):
    achievements: list[AchievementSchema]


class OrbSkinSchema(BaseModel):
    id: str
    name: str
    asset_key: str
    preview_asset_key: str
    enabled: bool
    assets: "OrbSkinAssetsSchema"

    @field_validator("asset_key", "preview_asset_key")
    @classmethod
    def validate_asset_key(cls, value: str) -> str:
        if re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
            raise ValueError("Orb skin asset keys may contain only letters, numbers, underscores, and hyphens")
        return value


class OrbSkinCollisionSoundSchema(BaseModel):
    url: AnyHttpUrl
    mime_type: str = "audio/mpeg"


class OrbSkinAssetsSchema(BaseModel):
    version: str
    preview_url: AnyHttpUrl
    texture_url: AnyHttpUrl
    detail_url: AnyHttpUrl
    collision_sound: OrbSkinCollisionSoundSchema


class OrbSkinListResponse(BaseModel):
    skins: list[OrbSkinSchema]
