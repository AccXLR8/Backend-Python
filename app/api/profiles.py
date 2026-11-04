from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.database.session import get_session
from app.profiles.schemas import (
    AchievementListResponse,
    AvatarListResponse,
    ProfileResponse,
    ProfileUpdateRequest,
    OrbSkinListResponse,
)
from app.profiles.service import profile_service

router = APIRouter()
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
CurrentUserDependency = Annotated[User, Depends(get_current_user)]


@router.get("/avatars", response_model=AvatarListResponse)
async def list_avatars(session: SessionDependency):
    return AvatarListResponse(avatars=await profile_service.list_avatars(session))


@router.get("/achievements", response_model=AchievementListResponse)
async def list_achievements(session: SessionDependency):
    return AchievementListResponse(
        achievements=await profile_service.list_achievements(session)
    )


@router.get("/me", response_model=ProfileResponse)
async def get_my_profile(
    user: CurrentUserDependency,
    session: SessionDependency,
):
    return await profile_service.get_profile_by_user_id(session, uuid.UUID(user.id))


@router.patch("/me", response_model=ProfileResponse)
async def update_my_profile(
    body: ProfileUpdateRequest,
    user: CurrentUserDependency,
    session: SessionDependency,
):
    try:
        profile = await profile_service.update_profile(
            session,
            uuid.UUID(user.id),
            username=body.username,
            avatar_id=body.avatar_id,
            equipped_orb_skin_id=body.equipped_orb_skin_id,
            equipped_board_skin_id=body.equipped_board_skin_id,
            nationality=body.nationality,
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken",
        ) from exc
    return profile


@router.get("/{username}", response_model=ProfileResponse)
async def get_public_profile(
    username: str,
    session: SessionDependency,
):
    return await profile_service.get_profile_by_username(session, username)
