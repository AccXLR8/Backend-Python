from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import models as db


async def grant_orb_skins(
    session: AsyncSession,
    user_id: uuid.UUID,
    skin_ids: Iterable[str],
) -> None:
    """Add skins idempotently, using native upserts on supported databases."""
    unique_ids = sorted(set(skin_ids))
    if not unique_ids:
        return

    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        for skin_id in unique_ids:
            if await session.get(db.UserOrbSkinModel, {"user_id": user_id, "skin_id": skin_id}) is None:
                session.add(db.UserOrbSkinModel(user_id=user_id, skin_id=skin_id))
        return

    statement = insert(db.UserOrbSkinModel).values(
        [{"user_id": user_id, "skin_id": skin_id} for skin_id in unique_ids]
    )
    await session.execute(statement.on_conflict_do_nothing(index_elements=["user_id", "skin_id"]))
