from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.database import models as db
from app.profiles.schemas import (
    BoardSkinSchema,
    GemPackCheckoutResponse,
    GemPackPurchaseResponse,
    GemPackSchema,
    OrbSkinGemPurchaseResponse,
    SkinBundlePurchaseResponse,
    SkinBundleSchema,
    WalletResponse,
)
from app.profiles.service import profile_service
from app.skins.inventory import grant_orb_skins

logger = logging.getLogger("app.shop")
RAZORPAY_API = "https://api.razorpay.com/v1"


class ShopService:
    """Keeps wallet changes and skin ownership server-authoritative and atomic."""

    async def get_wallet(self, session: AsyncSession, user_id: uuid.UUID) -> WalletResponse:
        wallet = await self._ensure_wallet(session, user_id)
        return WalletResponse(balance=wallet.gem_balance)

    async def list_gem_packs(self, session: AsyncSession) -> list[GemPackSchema]:
        packs = (
            await session.execute(
                select(db.GemPackModel)
                .where(db.GemPackModel.enabled.is_(True))
                .order_by(db.GemPackModel.sort_order, db.GemPackModel.gems_amount, db.GemPackModel.id)
            )
        ).scalars().all()
        return [
            GemPackSchema(
                id=pack.id,
                name=pack.name,
                gems_amount=pack.gems_amount,
                price_amount_minor=pack.price_amount_minor,
                price_currency=pack.price_currency,
            )
            for pack in packs
        ]

    async def list_skin_bundles(self, session: AsyncSession) -> list[SkinBundleSchema]:
        rows = (await session.execute(
            select(db.SkinBundleModel, db.BoardSkinModel)
            .join(db.BoardSkinModel, db.BoardSkinModel.id == db.SkinBundleModel.board_skin_id)
            .where(db.SkinBundleModel.enabled.is_(True), db.BoardSkinModel.enabled.is_(True))
            .order_by(db.SkinBundleModel.rarity, db.SkinBundleModel.name)
        )).all()
        orb_skins = {skin.id: skin for skin in await profile_service.list_orb_skins(session)}
        result: list[SkinBundleSchema] = []
        for bundle, board in rows:
            orb = orb_skins.get(bundle.orb_skin_id)
            if orb is None or not orb.enabled:
                continue
            result.append(SkinBundleSchema(
                id=bundle.id,
                name=bundle.name,
                rarity=bundle.rarity,
                price_gems=bundle.price_gems,
                board_skin=BoardSkinSchema.model_validate(board),
                orb_skin=orb,
            ))
        return result

    async def purchase_skin_bundle(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        bundle_id: str,
    ) -> SkinBundlePurchaseResponse:
        bundle = await session.get(db.SkinBundleModel, bundle_id)
        if bundle is None or not bundle.enabled:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Skin bundle not found")
        board = await session.get(db.BoardSkinModel, bundle.board_skin_id)
        orb = await session.get(db.OrbSkinModel, bundle.orb_skin_id)
        if board is None or not board.enabled or orb is None or not orb.enabled:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Skin bundle is unavailable")

        already_owns_orb = await session.get(db.UserOrbSkinModel, {"user_id": user_id, "skin_id": bundle.orb_skin_id})
        already_owns_board = await session.get(db.UserBoardSkinModel, {"user_id": user_id, "skin_id": bundle.board_skin_id})
        if already_owns_orb is not None and already_owns_board is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "You already own this bundle")

        await self._ensure_wallet(session, user_id)
        wallet = (await session.execute(
            select(db.UserGemWalletModel).where(db.UserGemWalletModel.user_id == user_id)
            .with_for_update().execution_options(populate_existing=True)
        )).scalar_one()
        # The wallet lock serializes competing bundle purchases by this player.
        # Re-read both entitlements after acquiring it to avoid charging twice.
        already_owns_orb = (await session.execute(
            select(db.UserOrbSkinModel.skin_id).where(
                db.UserOrbSkinModel.user_id == user_id,
                db.UserOrbSkinModel.skin_id == bundle.orb_skin_id,
            )
        )).scalar_one_or_none() is not None
        already_owns_board = (await session.execute(
            select(db.UserBoardSkinModel.skin_id).where(
                db.UserBoardSkinModel.user_id == user_id,
                db.UserBoardSkinModel.skin_id == bundle.board_skin_id,
            )
        )).scalar_one_or_none() is not None
        if already_owns_orb and already_owns_board:
            raise HTTPException(status.HTTP_409_CONFLICT, "You already own this bundle")
        if wallet.gem_balance < bundle.price_gems:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Not enough Gems. This bundle costs {bundle.price_gems}; your balance is {wallet.gem_balance}.",
            )

        wallet.gem_balance -= bundle.price_gems
        wallet.updated_at = datetime.now(UTC)
        session.add(db.WalletTransactionModel(
            user_id=user_id,
            amount_delta=-bundle.price_gems,
            reason="skin_bundle",
            reference_id=bundle.id,
        ))
        await grant_orb_skins(session, user_id, [bundle.orb_skin_id])
        if not already_owns_board:
            session.add(db.UserBoardSkinModel(user_id=user_id, skin_id=bundle.board_skin_id))
        await session.flush()
        owned_orbs = (await session.execute(
            select(db.UserOrbSkinModel.skin_id).where(db.UserOrbSkinModel.user_id == user_id)
        )).scalars().all()
        owned_boards = (await session.execute(
            select(db.UserBoardSkinModel.skin_id).where(db.UserBoardSkinModel.user_id == user_id)
        )).scalars().all()
        return SkinBundlePurchaseResponse(
            status="purchased",
            bundle_id=bundle.id,
            gems_spent=bundle.price_gems,
            gem_balance=wallet.gem_balance,
            owned_orb_skin_ids=sorted(set(owned_orbs) | {"classic"}),
            owned_board_skin_ids=sorted(set(owned_boards) | {"classic-grid"}),
        )

    async def create_gem_pack_checkout(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        pack_id: str,
    ) -> GemPackCheckoutResponse:
        settings = get_settings()
        self._require_payment_config(settings.razorpay_key_id, settings.razorpay_key_secret)
        pack = await session.get(db.GemPackModel, pack_id)
        if pack is None or not pack.enabled:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Gem pack not found")

        await self._ensure_wallet(session, user_id)
        purchase_id = uuid.uuid4()
        provider_order = await self._create_provider_order(
            key_id=settings.razorpay_key_id or "",
            key_secret=settings.razorpay_key_secret or "",
            amount=pack.price_amount_minor,
            currency=pack.price_currency,
            receipt=purchase_id.hex,
            notes={"purchase_id": str(purchase_id), "pack_id": pack.id},
        )
        order_id = provider_order.get("id")
        if not isinstance(order_id, str) or not order_id.startswith("order_"):
            logger.error("Razorpay returned an invalid order id")
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Payment provider returned an invalid order")
        if provider_order.get("amount") != pack.price_amount_minor or provider_order.get("currency") != pack.price_currency:
            logger.error("Razorpay order amount or currency did not match the stored Gem pack price")
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Payment provider returned an unexpected order price")

        session.add(
            db.GemPackPurchaseModel(
                id=purchase_id,
                user_id=user_id,
                pack_id=pack.id,
                razorpay_order_id=order_id,
                amount_minor=pack.price_amount_minor,
                currency=pack.price_currency,
                gems_amount=pack.gems_amount,
            )
        )
        await session.flush()
        return GemPackCheckoutResponse(
            purchase_id=purchase_id,
            key_id=settings.razorpay_key_id or "",
            order_id=order_id,
            amount=pack.price_amount_minor,
            currency=pack.price_currency,
            gems_amount=pack.gems_amount,
            pack_name=pack.name,
        )

    async def verify_gem_pack_checkout(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        order_id: str,
        payment_id: str,
        signature: str,
    ) -> GemPackPurchaseResponse:
        settings = get_settings()
        self._require_payment_config(settings.razorpay_key_id, settings.razorpay_key_secret)
        purchase = await self._get_purchase(session, user_id=user_id, order_id=order_id)
        self._verify_checkout_signature(
            order_id=purchase.razorpay_order_id,
            payment_id=payment_id,
            signature=signature,
            secret=settings.razorpay_key_secret or "",
        )
        if purchase.status == "paid":
            if purchase.razorpay_payment_id != payment_id:
                raise HTTPException(status.HTTP_409_CONFLICT, "This order was completed with another payment")
            wallet = await self._ensure_wallet(session, user_id)
            return GemPackPurchaseResponse(
                status="credited",
                gems_added=purchase.gems_amount,
                gem_balance=wallet.gem_balance,
            )

        payment = await self._fetch_provider_payment(
            settings.razorpay_key_id or "",
            settings.razorpay_key_secret or "",
            payment_id,
        )
        self._validate_captured_payment(payment, purchase)

        purchase = await self._get_purchase(session, user_id=user_id, order_id=order_id, lock=True)
        if purchase.status != "paid":
            await self._credit_gem_pack_purchase(session, purchase, payment_id)
        elif purchase.razorpay_payment_id != payment_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "This order was completed with another payment")
        await session.flush()
        wallet = await self._ensure_wallet(session, user_id)
        return GemPackPurchaseResponse(
            status="credited",
            gems_added=purchase.gems_amount,
            gem_balance=wallet.gem_balance,
        )

    async def process_razorpay_webhook(
        self,
        session: AsyncSession,
        *,
        body: bytes,
        signature: str | None,
    ) -> bool:
        """Verify raw provider bytes; only captured Gem-pack payments credit balances."""
        webhook_secret = get_settings().razorpay_webhook_secret
        if not webhook_secret:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Payment webhook is not configured")
        if not signature:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing Razorpay webhook signature")
        expected = hmac.new(webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid Razorpay webhook signature")

        try:
            event = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid webhook payload") from exc
        if not isinstance(event, dict) or event.get("event") != "payment.captured":
            return False
        payment = self._webhook_payment_entity(event)
        if not payment:
            return False
        order_id, payment_id = payment.get("order_id"), payment.get("id")
        if not isinstance(order_id, str) or not isinstance(payment_id, str):
            return False

        purchase = (
            await session.execute(
                select(db.GemPackPurchaseModel)
                .where(db.GemPackPurchaseModel.razorpay_order_id == order_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if purchase is None:
            # Other products may share this Razorpay account; ignore their events.
            return False
        self._validate_captured_payment(payment, purchase)
        if purchase.status == "paid":
            return False
        await self._credit_gem_pack_purchase(session, purchase, payment_id)
        await session.flush()
        return True

    async def purchase_orb_skin(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        skin_id: str,
    ) -> OrbSkinGemPurchaseResponse:
        skin = await session.get(db.OrbSkinModel, skin_id)
        if skin is None or not skin.enabled:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Orb skin not found")
        if skin.price_gems is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "This orb skin is not for sale")
        if await session.get(db.UserOrbSkinModel, {"user_id": user_id, "skin_id": skin_id}):
            raise HTTPException(status.HTTP_409_CONFLICT, "You already own this orb skin")

        await self._ensure_wallet(session, user_id)
        wallet = (
            await session.execute(
                select(db.UserGemWalletModel)
                .where(db.UserGemWalletModel.user_id == user_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        # Recheck after taking the wallet lock so simultaneous clicks cannot double-spend.
        if await session.get(db.UserOrbSkinModel, {"user_id": user_id, "skin_id": skin_id}):
            raise HTTPException(status.HTTP_409_CONFLICT, "You already own this orb skin")
        if wallet.gem_balance < skin.price_gems:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Not enough Gems. This skin costs {skin.price_gems}; your balance is {wallet.gem_balance}.",
            )

        wallet.gem_balance -= skin.price_gems
        wallet.updated_at = datetime.now(UTC)
        session.add(
            db.WalletTransactionModel(
                user_id=user_id,
                amount_delta=-skin.price_gems,
                reason="orb_skin",
                reference_id=skin.id,
            )
        )
        await grant_orb_skins(session, user_id, [skin.id])
        await session.flush()
        owned = (
            await session.execute(
                select(db.UserOrbSkinModel.skin_id)
                .where(db.UserOrbSkinModel.user_id == user_id)
                .order_by(db.UserOrbSkinModel.skin_id)
            )
        ).scalars().all()
        return OrbSkinGemPurchaseResponse(
            status="purchased",
            skin_id=skin.id,
            gems_spent=skin.price_gems,
            gem_balance=wallet.gem_balance,
            owned_orb_skin_ids=sorted(set(owned) | {"classic"}),
        )

    async def _credit_gem_pack_purchase(
        self,
        session: AsyncSession,
        purchase: db.GemPackPurchaseModel,
        payment_id: str,
    ) -> None:
        wallet = await self._ensure_wallet(session, purchase.user_id)
        wallet = (
            await session.execute(
                select(db.UserGemWalletModel)
                .where(db.UserGemWalletModel.user_id == purchase.user_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        purchase.status = "paid"
        purchase.razorpay_payment_id = payment_id
        purchase.paid_at = datetime.now(UTC)
        wallet.gem_balance += purchase.gems_amount
        wallet.updated_at = datetime.now(UTC)
        session.add(
            db.WalletTransactionModel(
                user_id=purchase.user_id,
                amount_delta=purchase.gems_amount,
                reason="gem_pack",
                reference_id=str(purchase.id),
            )
        )

    @staticmethod
    async def _ensure_wallet(session: AsyncSession, user_id: uuid.UUID) -> db.UserGemWalletModel:
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        elif dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        else:
            wallet = await session.get(db.UserGemWalletModel, user_id)
            if wallet is None:
                session.add(db.UserGemWalletModel(user_id=user_id))
                await session.flush()
                wallet = await session.get(db.UserGemWalletModel, user_id)
            return wallet

        statement = insert(db.UserGemWalletModel).values(user_id=user_id, gem_balance=0)
        await session.execute(statement.on_conflict_do_nothing(index_elements=["user_id"]))
        wallet = await session.get(db.UserGemWalletModel, user_id)
        if wallet is None:
            raise RuntimeError("Wallet row was not created")
        return wallet

    @staticmethod
    async def _get_purchase(
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        order_id: str,
        lock: bool = False,
    ) -> db.GemPackPurchaseModel:
        query = select(db.GemPackPurchaseModel).where(
            db.GemPackPurchaseModel.user_id == user_id,
            db.GemPackPurchaseModel.razorpay_order_id == order_id,
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        purchase = (await session.execute(query)).scalar_one_or_none()
        if purchase is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Gem purchase order not found")
        return purchase

    @staticmethod
    async def _create_provider_order(
        *, key_id: str, key_secret: str, amount: int, currency: str, receipt: str, notes: dict[str, str]
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                base_url=RAZORPAY_API,
                auth=(key_id, key_secret),
                timeout=httpx.Timeout(10.0),
            ) as client:
                response = await client.post(
                    "/orders",
                    json={"amount": amount, "currency": currency, "receipt": receipt, "notes": notes},
                )
                response.raise_for_status()
                result = response.json()
        except (httpx.HTTPError, ValueError):
            logger.exception("Could not create Razorpay Gem-pack order")
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "The payment provider could not start checkout. Please try again.",
            ) from None
        return result if isinstance(result, dict) else {}

    @staticmethod
    async def _fetch_provider_payment(key_id: str, key_secret: str, payment_id: str) -> dict[str, Any]:
        if not payment_id.startswith("pay_") or not payment_id.replace("_", "").isalnum():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid Razorpay payment id")
        try:
            async with httpx.AsyncClient(
                base_url=RAZORPAY_API,
                auth=(key_id, key_secret),
                timeout=httpx.Timeout(10.0),
            ) as client:
                response = await client.get(f"/payments/{payment_id}")
                response.raise_for_status()
                result = response.json()
        except (httpx.HTTPError, ValueError):
            logger.exception("Could not verify Razorpay Gem-pack payment")
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "Payment status could not be confirmed yet. Please retry shortly.",
            ) from None
        if not isinstance(result, dict):
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Payment provider returned an invalid payment")
        return result

    @staticmethod
    def _verify_checkout_signature(*, order_id: str, payment_id: str, signature: str, secret: str) -> None:
        if not payment_id.startswith("pay_") or not payment_id.replace("_", "").isalnum():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid Razorpay payment id")
        expected = hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Payment verification failed")

    @staticmethod
    def _validate_captured_payment(payment: dict[str, Any], purchase: db.GemPackPurchaseModel) -> None:
        if (
            payment.get("status") != "captured"
            or payment.get("order_id") != purchase.razorpay_order_id
            or payment.get("amount") != purchase.amount_minor
            or payment.get("currency") != purchase.currency
        ):
            raise HTTPException(status.HTTP_409_CONFLICT, "Payment is not captured for this Gem-pack order yet.")

    @staticmethod
    def _webhook_payment_entity(event: dict[str, Any]) -> dict[str, Any] | None:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return None
        wrapper = payload.get("payment")
        if not isinstance(wrapper, dict):
            return None
        entity = wrapper.get("entity")
        return entity if isinstance(entity, dict) else None

    @staticmethod
    def _require_payment_config(key_id: str | None, key_secret: str | None) -> None:
        if not key_id or not key_secret:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Gem-pack payments are not configured yet.")


shop_service = ShopService()
