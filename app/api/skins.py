from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_session
from app.profiles.schemas import OrbSkinListResponse
from app.profiles.service import profile_service

router = APIRouter()


@router.get("/orbs", response_model=OrbSkinListResponse)
async def list_orb_skins(session: Annotated[AsyncSession, Depends(get_session)]):
    return OrbSkinListResponse(skins=await profile_service.list_orb_skins(session))
