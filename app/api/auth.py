from __future__ import annotations

import hashlib
import random
import re
import string
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.auth.schemas import (
    ChangePasswordRequest,
    GoogleSignInRequest,
    GuestRequest,
    LoginRequest,
    RegisterRequest,
)
from app.auth.service import (
    GoogleAuthConfigurationError,
    GoogleAuthenticationError,
    auth_service,
)
from app.database.repositories import UserRepository
from app.database import models as db
from app.database.session import get_session
from app.profiles.service import profile_service
from app.rate_limit import enforce_auth_attempt_limit, enforce_password_change_limit

router = APIRouter(prefix="/auth", tags=["auth"])


class TokenUser(BaseModel):
    id: uuid.UUID
    username: str
    is_guest: bool
    nationality: str = "global"
    email: str | None = None
    email_verified: bool = False


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: TokenUser


class PasswordChangeResponse(BaseModel):
    status: Literal["password_changed"]


@router.post("/google", response_model=TokenResponse)
async def google_sign_in(
    body: GoogleSignInRequest,
    session: AsyncSession = Depends(get_session),
    request: Request = None,
):
    await enforce_auth_attempt_limit(request, "google")
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
    try:
        if user is None:
            username = await _google_username(repo, identity)
            user = await repo.create(
                username=username,
                password_hash=None,
                google_subject=identity["subject"],
                email=identity["email"],
                is_guest=False,
            )
            user.email_verified = True
            profile_service.create_records_for_new_user(
                session,
                user.id,
                created_at=user.created_at,
            )
        else:
            # Keep the game-facing username stable while refreshing mutable profile data.
            user.email = identity["email"]
            user.email_verified = True
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # A simultaneous callback for the same Google subject is safe to retry:
        # use the account committed by the request that won the unique constraint.
        user = await repo.get_by_google_subject(identity["subject"])
        if user is None:
            if await repo.get_by_email(identity["email"]):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Email address is already associated with another account",
                ) from exc
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Could not create the Google account; please try again",
            ) from exc
    token = auth_service.create_access_token(user=user)
    return TokenResponse(access_token=token, user=_serialize_user(user))


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register_user(
    body: RegisterRequest,
    session: AsyncSession = Depends(get_session),
    request: Request = None,
):
    await enforce_auth_attempt_limit(request, "register")
    repo = UserRepository(session)
    existing = await repo.get_registered_by_username(body.username)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already taken")
    if await repo.get_by_email(str(body.email)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email address already in use")

    password_hash = auth_service.hash_password(body.password)
    try:
        user = await repo.create(
            username=body.username,
            email=str(body.email),
            email_verified=False,
            password_hash=password_hash,
            is_guest=False,
            nationality=body.nationality,
        )
        profile_service.create_records_for_new_user(session, user.id, created_at=user.created_at)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if await repo.get_registered_by_username(body.username):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already taken") from exc
        if await repo.get_by_email(str(body.email)):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email address already in use") from exc
        raise

    token = auth_service.create_access_token(user=user)
    return TokenResponse(access_token=token, user=_serialize_user(user))


@router.post("/login", response_model=TokenResponse)
async def login_user(
    body: LoginRequest,
    session: AsyncSession = Depends(get_session),
    request: Request = None,
):
    await enforce_auth_attempt_limit(request, "login")
    repo = UserRepository(session)
    user = await repo.get_registered_by_username(body.username)
    if not user or not auth_service.verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if auth_service.password_hash_needs_upgrade(user.password_hash):
        user.password_hash = auth_service.hash_password(body.password)
        await session.commit()

    token = auth_service.create_access_token(user=user)
    return TokenResponse(access_token=token, user=_serialize_user(user))


@router.post("/guest", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def guest_user(
    body: GuestRequest | None = None,
    session: AsyncSession = Depends(get_session),
    request: Request = None,
):
    await enforce_auth_attempt_limit(request, "guest")
    repo = UserRepository(session)
    username = _generate_guest_username(body.username if body else None)
    user = await repo.create(username=username, password_hash=None, is_guest=True)
    profile_service.create_records_for_new_user(session, user.id, created_at=user.created_at)
    await session.commit()
    token = auth_service.create_access_token(user=user)
    return TokenResponse(access_token=token, user=_serialize_user(user))


@router.post("/change-password", response_model=PasswordChangeResponse)
async def change_password(
    body: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Change a registered password account's password after checking the old one."""
    await enforce_password_change_limit(user.id)
    db_user = await session.get(db.UserModel, uuid.UUID(user.id))
    if db_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid account session")
    if db_user.is_guest:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Guest accounts do not have passwords")
    if not auth_service.verify_password(body.current_password, db_user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect")
    if auth_service.verify_password(body.new_password, db_user.password_hash):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Choose a different password")

    db_user.password_hash = auth_service.hash_password(body.new_password)
    await session.commit()
    return PasswordChangeResponse(status="password_changed")


async def _google_username(repo: UserRepository, identity: dict[str, str]) -> str:
    source = identity.get("name") or identity["email"].split("@", 1)[0]
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", source).strip("-_") or "player"
    if len(base) < 3:
        base = f"{base}-p"
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
    return TokenUser(
        id=user.id if isinstance(user.id, uuid.UUID) else uuid.UUID(str(user.id)),
        username=user.username,
        is_guest=user.is_guest,
        nationality=getattr(user, "nationality", "global") or "global",
        email=getattr(user, "email", None),
        email_verified=getattr(user, "email_verified", False),
    )
