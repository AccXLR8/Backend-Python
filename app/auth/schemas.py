from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


USERNAME_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")


def normalize_username(value: str, *, maximum: int = 24) -> str:
    normalized = value.strip()
    if not 3 <= len(normalized) <= maximum:
        raise ValueError(f"Username must be between 3 and {maximum} characters")
    if USERNAME_PATTERN.fullmatch(normalized) is None:
        raise ValueError("Username may contain only letters, numbers, underscores, and hyphens")
    return normalized


def validate_new_password(value: str) -> str:
    if len(value) < 8:
        raise ValueError("Password must be at least 8 characters long")
    if not any(character.islower() for character in value):
        raise ValueError("Password must include a lowercase letter")
    if not any(character.isupper() for character in value):
        raise ValueError("Password must include an uppercase letter")
    if not any(character.isdigit() for character in value):
        raise ValueError("Password must include a number")
    if not any(not character.isalnum() for character in value):
        raise ValueError("Password must include a symbol")
    return value


class AuthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterRequest(AuthRequest):
    username: str = Field(min_length=3, max_length=24)
    password: str = Field(min_length=8, max_length=128)
    nationality: str = Field(default="global", min_length=2, max_length=8)

    @field_validator("username", mode="before")
    @classmethod
    def clean_username(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("Username must be text")
        return normalize_username(value)

    @field_validator("password")
    @classmethod
    def check_password_strength(cls, value: str) -> str:
        return validate_new_password(value)

    @field_validator("nationality")
    @classmethod
    def validate_nationality(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized == "GLOBAL":
            return "global"
        if len(normalized) != 2 or not normalized.isalpha() or not normalized.isascii():
            raise ValueError('Nationality must be an ISO alpha-2 country code or "global"')
        return normalized


class LoginRequest(AuthRequest):
    # Keep the wider legacy username range so existing accounts remain accessible.
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def clean_username(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("Username must be text")
        normalized = value.strip()
        if not 3 <= len(normalized) <= 64:
            raise ValueError("Username must be between 3 and 64 characters")
        return normalized


class GuestRequest(AuthRequest):
    username: str | None = Field(default=None, max_length=64)

    @field_validator("username", mode="before")
    @classmethod
    def clean_username(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("Username must be text")
        return normalize_username(value)


class GoogleSignInRequest(AuthRequest):
    credential: str = Field(min_length=100, max_length=8192)


class ChangePasswordRequest(AuthRequest):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def check_password_strength(cls, value: str) -> str:
        return validate_new_password(value)
