from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta
from functools import partial
from typing import Any

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token as google_id_token
from jose import JWTError, jwt
from passlib.context import CryptContext
from passlib.exc import InvalidHashError, UnknownHashError

from app.auth.models import User
from app.config.settings import get_settings
from app.database import models as db


class AuthenticationError(Exception):
    """Raised when a backend session token cannot be validated."""


class GoogleAuthenticationError(Exception):
    """Raised when a Google ID token cannot be validated."""


class GoogleAuthConfigurationError(Exception):
    """Raised when Google sign-in is not configured on the backend."""


class AuthService:
    def __init__(self) -> None:
        self._algorithm = "HS256"
        # New accounts use PBKDF2-SHA256, which safely supports the full API
        # password length. Existing bcrypt hashes remain verifiable and are
        # upgraded after a successful login.
        self._password_context = CryptContext(
            schemes=["pbkdf2_sha256", "bcrypt"],
            deprecated=["bcrypt"],
            pbkdf2_sha256__rounds=600_000,
        )
        self._token_ttl = timedelta(hours=24)

    async def authenticate(self, token: str) -> User:
        payload = self._decode_token(token)
        now = datetime.utcnow()
        return User(
            id=payload["sub"],
            username=payload["username"],
            created_at=now,
            updated_at=now,
            is_active=payload.get("is_active", True),
            is_bot=payload.get("is_bot", False),
            is_guest=payload.get("is_guest", False),
            nationality=payload.get("nationality", "global"),
        )

    async def verify_google_credential(self, credential: str) -> dict[str, Any]:
        settings = get_settings()
        if not settings.google_client_id:
            raise GoogleAuthConfigurationError("Google sign-in is not configured")

        verify = partial(
            google_id_token.verify_oauth2_token,
            credential,
            GoogleRequest(),
            settings.google_client_id,
        )
        try:
            claims = await asyncio.to_thread(verify)
        except (GoogleAuthError, ValueError) as exc:
            raise GoogleAuthenticationError("Invalid Google credential") from exc

        subject = claims.get("sub")
        email = claims.get("email")
        if not subject or not email or claims.get("email_verified") is not True:
            raise GoogleAuthenticationError("Google account email is not verified")

        return {
            "subject": str(subject),
            "email": str(email).strip().lower(),
            "name": str(claims.get("name") or "").strip(),
        }

    def hash_password(self, password: str) -> str:
        return self._password_context.hash(password)

    def verify_password(self, plain_password: str, hashed_password: str | None) -> bool:
        if not hashed_password:
            return False
        try:
            return self._password_context.verify(plain_password, hashed_password)
        except (InvalidHashError, UnknownHashError, ValueError, TypeError):
            return False

    def password_hash_needs_upgrade(self, hashed_password: str | None) -> bool:
        if not hashed_password:
            return False
        try:
            return self._password_context.needs_update(hashed_password)
        except (InvalidHashError, UnknownHashError, ValueError, TypeError):
            return False

    def create_access_token(self, *, user: db.UserModel | User) -> str:
        payload = self._build_payload(user)
        settings = get_settings()
        return jwt.encode(payload, settings.secret_key, algorithm=self._algorithm)

    def _build_payload(self, user: db.UserModel | User) -> dict[str, Any]:
        if isinstance(user, db.UserModel):
            user_id = str(user.id)
            username = user.username
            is_guest = user.is_guest
            nationality = getattr(user, "nationality", "global") or "global"
        else:
            user_id = user.id
            username = user.username
            is_guest = getattr(user, "is_guest", False)
            nationality = getattr(user, "nationality", "global") or "global"
        now = datetime.utcnow()
        expire = now + self._token_ttl
        return {
            "sub": user_id,
            "username": username,
            "is_guest": is_guest,
            "nationality": nationality,
            "is_active": True,
            "iat": int(now.timestamp()),
            "exp": int(expire.timestamp()),
        }

    def _decode_token(self, token: str) -> dict[str, Any]:
        if not token:
            raise AuthenticationError("Missing bearer token")
        settings = get_settings()
        try:
            payload = jwt.decode(token, settings.secret_key, algorithms=[self._algorithm])
        except JWTError as exc:  # pragma: no cover - jose already tested
            raise AuthenticationError("Invalid token") from exc

        subject = payload.get("sub")
        username = payload.get("username") or payload.get("preferred_username")
        if subject is None or username is None:
            raise AuthenticationError("Token missing required claims")

        try:
            payload["sub"] = str(uuid.UUID(str(subject)))
        except (ValueError, TypeError) as exc:
            raise AuthenticationError("Token subject must be a UUID") from exc
        payload["username"] = username
        return payload

    def model_to_user(self, model: db.UserModel) -> User:
        return User(
            id=str(model.id),
            username=model.username,
            created_at=model.created_at,
            updated_at=model.updated_at,
            is_guest=model.is_guest,
            nationality=getattr(model, "nationality", "global") or "global",
        )


auth_service = AuthService()
