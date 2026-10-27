from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.auth.service import AuthenticationError


class DummyAuthService:
    def __init__(self, expected_token: str = "token123") -> None:
        self.expected_token = expected_token

    async def authenticate(self, token: str) -> User:
        if token != self.expected_token:
            raise AuthenticationError("invalid")
        now = datetime.now(UTC)
        return User(id="user", username="demo", created_at=now, updated_at=now)


@pytest.mark.asyncio
async def test_get_current_user_accepts_lowercase_bearer(monkeypatch):
    monkeypatch.setattr("app.auth.dependencies.auth_service", DummyAuthService())
    user = await get_current_user(authorization="bearer token123")
    assert user.username == "demo"


@pytest.mark.asyncio
async def test_get_current_user_missing_token_raises(monkeypatch):
    monkeypatch.setattr("app.auth.dependencies.auth_service", DummyAuthService())
    with pytest.raises(HTTPException):
        await get_current_user(authorization="")
