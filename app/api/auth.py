from __future__ import annotations

import hashlib
import random
import re
import string
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, constr, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import (
    GoogleAuthConfigurationError,
    GoogleAuthenticationError,
    auth_service,
)
from app.database.repositories import UserRepository
from app.database.session import get_session
from app.profiles.service import profile_service

router = APIRouter(prefix="/auth", tags=["auth"])


class TokenUser(BaseModel):
    id: uuid.UUID
    username: str
    is_guest: bool
    nationality: str = "global"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: TokenUser


class RegisterRequest(BaseModel):
    username: constr(min_length=3, max_length=64)
    password: constr(min_length=6, max_length=128)
    nationality: str = Field(default="global", min_length=2, max_length=8)

    @field_validator("nationality")
    @classmethod
    def validate_nationality(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized == "GLOBAL":
            return "global"
        if len(normalized) != 2 or not normalized.isalpha() or not normalized.isascii():
            raise ValueError('Nationality must be an ISO alpha-2 country code or "global"')
        return normalized


class LoginRequest(BaseModel):
    username: constr(min_length=3, max_length=64)
    password: constr(min_length=6, max_length=128)


class GuestRequest(BaseModel):
    username: str | None = Field(default=None, max_length=64)


class GoogleSignInRequest(BaseModel):
    credential: str = Field(min_length=100, max_length=8192)


@router.post("/google", response_model=TokenResponse)
async def google_sign_in(
    body: GoogleSignInRequest,
    session: AsyncSession = Depends(get_session),
):
    try:
        identity = await auth_service.verify_google_credential(body.credential)
    except GoogleAuthConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google sign-in is not configured",
        ) from exc
    except GoogleAuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google credential",
        ) from exc

    repo = UserRepository(session)
    user = await repo.get_by_google_subject(identity["subject"])
    if user is None:
        username = await _google_username(repo, identity)
        user = await repo.create(
            username=username,
            password_hash=None,
            google_subject=identity["subject"],
            email=identity["email"],
            is_guest=False,
        )
        profile_service.create_records_for_new_user(
            session,
            user.id,
            created_at=user.created_at,
        )
    else:
        # Keep the game-facing username stable while refreshing mutable profile data.
        user.email = identity["email"]

    await session.commit()
    token = auth_service.create_access_token(user=user)
    return TokenResponse(access_token=token, user=_serialize_user(user))


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register_user(body: RegisterRequest, session: AsyncSession = Depends(get_session)):
    repo = UserRepository(session)
    existing = await repo.get_registered_by_username(body.username)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already taken")

    password_hash = auth_service.hash_password(body.password)
    user = await repo.create(
        username=body.username,
        password_hash=password_hash,
        is_guest=False,
        nationality=body.nationality,
    )
    profile_service.create_records_for_new_user(session, user.id, created_at=user.created_at)
    await session.commit()

    token = auth_service.create_access_token(user=user)
    return TokenResponse(access_token=token, user=_serialize_user(user))


@router.post("/login", response_model=TokenResponse)
async def login_user(body: LoginRequest, session: AsyncSession = Depends(get_session)):
    repo = UserRepository(session)
    user = await repo.get_registered_by_username(body.username)
    if not user or not auth_service.verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if user.is_guest:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Guest accounts cannot log in with passwords")

    token = auth_service.create_access_token(user=user)
    return TokenResponse(access_token=token, user=_serialize_user(user))


@router.post("/guest", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def guest_user(body: GuestRequest | None = None, session: AsyncSession = Depends(get_session)):
    repo = UserRepository(session)
    username = _generate_guest_username(body.username if body else None)
    user = await repo.create(username=username, password_hash=None, is_guest=True)
    profile_service.create_records_for_new_user(session, user.id, created_at=user.created_at)
    await session.commit()
    token = auth_service.create_access_token(user=user)
    return TokenResponse(access_token=token, user=_serialize_user(user))


async def _google_username(repo: UserRepository, identity: dict[str, str]) -> str:
    source = identity.get("name") or identity["email"].split("@", 1)[0]
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", source).strip("-_") or "player"
    base = base[:24]
    if await repo.get_registered_by_username(base) is None:
        return base

    suffix = hashlib.sha256(identity["subject"].encode("utf-8")).hexdigest()[:8]
    return f"{base[:15]}-{suffix}"


def _generate_guest_username(desired: str | None = None) -> str:
    if desired:
        return desired
    suffix = "".join(random.choices(string.digits, k=6))
    return f"guest-{suffix}"


def _serialize_user(user) -> TokenUser:
    return TokenUser(id=user.id if isinstance(user.id, uuid.UUID) else uuid.UUID(str(user.id)), username=user.username, is_guest=user.is_guest, nationality=getattr(user, "nationality", "global") or "global")
