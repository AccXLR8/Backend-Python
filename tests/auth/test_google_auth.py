from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
from fastapi import HTTPException
from google.auth.exceptions import GoogleAuthError

from app.api import auth as auth_api
from app.auth.service import (
    AuthService,
    GoogleAuthenticationError,
)
from app.database.repositories import UserRepository


@pytest.mark.asyncio
async def test_google_credential_verification_accepts_verified_identity(monkeypatch):
    service = AuthService()
    monkeypatch.setattr(
        "app.auth.service.get_settings",
        lambda: SimpleNamespace(google_client_id="client.apps.googleusercontent.com"),
    )
    monkeypatch.setattr(
        "app.auth.service.google_id_token.verify_oauth2_token",
        lambda credential, request, audience: {
            "sub": "google-subject",
            "email": "Player@Example.com",
            "email_verified": True,
            "name": "Player One",
        },
    )

    identity = await service.verify_google_credential("google-id-token")

    assert identity == {
        "subject": "google-subject",
        "email": "player@example.com",
        "name": "Player One",
    }


@pytest.mark.asyncio
async def test_google_credential_verification_rejects_invalid_token(monkeypatch):
    service = AuthService()
    monkeypatch.setattr(
        "app.auth.service.get_settings",
        lambda: SimpleNamespace(google_client_id="client.apps.googleusercontent.com"),
    )

    def reject(*_args, **_kwargs):
        raise GoogleAuthError("invalid")

    monkeypatch.setattr("app.auth.service.google_id_token.verify_oauth2_token", reject)

    with pytest.raises(GoogleAuthenticationError):
        await service.verify_google_credential("invalid-google-id-token")


@pytest.mark.asyncio
async def test_google_sign_in_creates_user_and_backend_session(monkeypatch):
    user_id = uuid.uuid4()
    created_user = SimpleNamespace(
        id=user_id,
        username="Player-One",
        is_guest=False,
        email="player@example.com",
        created_at=datetime.now(timezone.utc),
    )
    session = SimpleNamespace(commit=AsyncMock(), add=lambda _record: None)
    verify = AsyncMock(
        return_value={
            "subject": "google-subject",
            "email": "player@example.com",
            "name": "Player One",
        }
    )
    monkeypatch.setattr(auth_api.auth_service, "verify_google_credential", verify)
    monkeypatch.setattr(auth_api.auth_service, "create_access_token", lambda *, user: "backend-token")
    monkeypatch.setattr(UserRepository, "get_by_google_subject", AsyncMock(return_value=None))
    monkeypatch.setattr(UserRepository, "get_registered_by_username", AsyncMock(return_value=None))
    create_user = AsyncMock(return_value=created_user)
    monkeypatch.setattr(UserRepository, "create", create_user)

    response = await auth_api.google_sign_in(
        auth_api.GoogleSignInRequest(credential="x" * 100),
        session=session,
    )

    assert response.access_token == "backend-token"
    assert response.user.id == user_id
    assert response.user.username == "Player-One"
    assert response.user.is_guest is False
    create_user.assert_awaited_once_with(
        username="Player-One",
        password_hash=None,
        google_subject="google-subject",
        email="player@example.com",
        is_guest=False,
    )
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_google_sign_in_returns_401_for_invalid_google_token(monkeypatch):
    session = SimpleNamespace(commit=AsyncMock())
    monkeypatch.setattr(
        auth_api.auth_service,
        "verify_google_credential",
        AsyncMock(side_effect=GoogleAuthenticationError("invalid")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await auth_api.google_sign_in(
            auth_api.GoogleSignInRequest(credential="x" * 100),
            session=session,
        )

    assert exc_info.value.status_code == 401
    session.commit.assert_not_awaited()
