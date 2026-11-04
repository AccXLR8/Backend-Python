from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.database.session import get_session
from app.profiles.schemas import (
    GemPackCheckoutVerificationRequest,
    GemPackCheckoutResponse,
    GemPackListResponse,
    GemPackPurchaseResponse,
    OrbSkinGemPurchaseResponse,
    SkinBundleListResponse,
    SkinBundlePurchaseResponse,
    WalletResponse,
)
from app.shop.service import shop_service

router = APIRouter()
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
CurrentUserDependency = Annotated[User, Depends(get_current_user)]


def _registered_user_id(user: User) -> uuid.UUID:
    if user.is_guest:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Create or sign into a player account to use the shop.",
        )
    return uuid.UUID(user.id)


@router.get("/wallet", response_model=WalletResponse)
async def read_wallet(user: CurrentUserDependency, session: SessionDependency):
    result = await shop_service.get_wallet(session, _registered_user_id(user))
    await session.commit()
    return result


@router.get("/gem-packs", response_model=GemPackListResponse)
async def list_gem_packs(session: SessionDependency):
    return GemPackListResponse(packs=await shop_service.list_gem_packs(session))


@router.get("/bundles", response_model=SkinBundleListResponse)
async def list_skin_bundles(session: SessionDependency):
    return SkinBundleListResponse(bundles=await shop_service.list_skin_bundles(session))


@router.post(
    "/gem-packs/{pack_id}/checkout",
    response_model=GemPackCheckoutResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_gem_pack_checkout(
    pack_id: str,
    user: CurrentUserDependency,
    session: SessionDependency,
):
    result = await shop_service.create_gem_pack_checkout(
        session,
        user_id=_registered_user_id(user),
        pack_id=pack_id,
    )
    await session.commit()
    return result


@router.post("/gem-packs/verify", response_model=GemPackPurchaseResponse)
async def verify_gem_pack_checkout(
    body: GemPackCheckoutVerificationRequest,
    user: CurrentUserDependency,
    session: SessionDependency,
):
    result = await shop_service.verify_gem_pack_checkout(
        session,
        user_id=_registered_user_id(user),
        order_id=body.order_id,
        payment_id=body.payment_id,
        signature=body.signature,
    )
    await session.commit()
    return result


@router.post("/orb-skins/{skin_id}/purchase", response_model=OrbSkinGemPurchaseResponse)
async def purchase_orb_skin(
    skin_id: str,
    user: CurrentUserDependency,
    session: SessionDependency,
):
    result = await shop_service.purchase_orb_skin(
        session,
        user_id=_registered_user_id(user),
        skin_id=skin_id,
    )
    await session.commit()
    return result


@router.post("/bundles/{bundle_id}/purchase", response_model=SkinBundlePurchaseResponse)
async def purchase_skin_bundle(
    bundle_id: str,
    user: CurrentUserDependency,
    session: SessionDependency,
):
    result = await shop_service.purchase_skin_bundle(
        session,
        user_id=_registered_user_id(user),
        bundle_id=bundle_id,
    )
    await session.commit()
    return result


@router.post("/razorpay/webhook")
async def razorpay_webhook(
    request: Request,
    session: SessionDependency,
    signature: Annotated[str | None, Header(alias="X-Razorpay-Signature")] = None,
):
    processed = await shop_service.process_razorpay_webhook(
        session,
        body=await request.body(),
        signature=signature,
    )
    await session.commit()
    return {"status": "processed" if processed else "ignored"}
