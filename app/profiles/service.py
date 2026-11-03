from __future__ import annotations

import uuid
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import HTTPException, status
from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.competitive.service import competitive_service
from app.config.settings import get_settings
from app.database import models as db
from app.profiles.schemas import (
    AchievementSchema,
    AvatarSchema,
    OrbSkinAssetsSchema,
    OrbSkinCollisionSoundSchema,
    OrbSkinSchema,
    PlayerStatsSchema,
    ProfileResponse,
)
from app.skins.inventory import grant_orb_skins

WIN_STREAK_ACHIEVEMENT = "WIN_STREAK_20"
RANKED_WIN_STREAK_ACHIEVEMENT = "RANKED_WIN_STREAK_20"


class ProfileService:
    def create_records_for_new_user(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        *,
        created_at: datetime | None = None,
    ) -> None:
        timestamp = created_at or datetime.now(UTC)
        session.add(
            db.UserProfileModel(
                user_id=user_id,
                avatar_id=1,
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        session.add(db.PlayerStatsModel(user_id=user_id, updated_at=timestamp))
        session.add(db.UserOrbSkinModel(user_id=user_id, skin_id="classic"))
        session.add(db.UserOrbSkinSelectionModel(user_id=user_id, skin_id="classic"))

    async def ensure_records(
        self,
        session: AsyncSession,
        user: db.UserModel,
    ) -> tuple[db.UserProfileModel, db.PlayerStatsModel]:
        profile = await session.get(db.UserProfileModel, user.id)
        stats = await session.get(db.PlayerStatsModel, user.id)
        if profile is None:
            profile = db.UserProfileModel(
                user_id=user.id,
                avatar_id=1,
                created_at=user.created_at,
                updated_at=user.updated_at,
            )
            session.add(profile)
        if stats is None:
            stats = db.PlayerStatsModel(user_id=user.id)
            session.add(stats)
        await session.flush()
        return profile, stats

    async def list_avatars(self, session: AsyncSession) -> list[AvatarSchema]:
        rows = (
            await session.execute(
                select(db.AvatarModel)
                .where(db.AvatarModel.enabled.is_(True))
                .order_by(db.AvatarModel.sort_order, db.AvatarModel.id)
            )
        ).scalars().all()
        return [self._avatar_schema(row) for row in rows]

    async def list_achievements(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID | None = None,
    ) -> list[AchievementSchema]:
        definitions = (
            await session.execute(
                select(db.AchievementModel)
                .where(db.AchievementModel.enabled.is_(True))
                .order_by(db.AchievementModel.id)
            )
        ).scalars().all()
        earned: dict[int, datetime] = {}
        reward_ids: dict[int, list[str]] = {}
        reward_rows = (
            await session.execute(
                select(
                    db.AchievementOrbSkinRewardModel.achievement_id,
                    db.AchievementOrbSkinRewardModel.skin_id,
                )
                .join(
                    db.OrbSkinModel,
                    db.OrbSkinModel.id == db.AchievementOrbSkinRewardModel.skin_id,
                )
                .where(db.OrbSkinModel.enabled.is_(True))
                .order_by(db.AchievementOrbSkinRewardModel.achievement_id,
                          db.AchievementOrbSkinRewardModel.skin_id)
            )
        ).all()
        for achievement_id, skin_id in reward_rows:
            reward_ids.setdefault(achievement_id, []).append(skin_id)
        if user_id is not None:
            earned_rows = (
                await session.execute(
                    select(db.UserAchievementModel).where(
                        db.UserAchievementModel.user_id == user_id
                    )
                )
            ).scalars().all()
            earned = {row.achievement_id: row.earned_at for row in earned_rows}
        return [
            AchievementSchema(
                code=definition.code,
                name=definition.name,
                description=definition.description,
                badge_image_url=definition.badge_image_url,
                earned_at=earned.get(definition.id),
                unlocked=definition.id in earned,
                orb_skin_reward_ids=reward_ids.get(definition.id, []),
            )
            for definition in definitions
        ]

    async def get_profile_by_user_id(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
    ) -> ProfileResponse:
        user = await session.get(db.UserModel, user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
        profile, stats = await self.ensure_records(session, user)
        owned, equipped = await self.ensure_orb_skin_records(session, user)
        avatar = await session.get(db.AvatarModel, profile.avatar_id)
        if avatar is None:  # seed data should guarantee this
            raise RuntimeError("Profile avatar does not exist")
        achievements = await self.list_achievements(session, user_id=user.id)
        return self._profile_response(user, avatar, stats, achievements, owned, equipped)

    async def ensure_orb_skin_records(self, session: AsyncSession, user: db.UserModel) -> tuple[list[str], str]:
        classic = await session.get(db.OrbSkinModel, "classic")
        if classic is None:
            classic = db.OrbSkinModel(id="classic", name="Classic Orb", asset_key="classic",
                                      preview_asset_key="classic", enabled=True)
            session.add(classic)
            await session.flush()
        owned = await session.get(db.UserOrbSkinModel, {"user_id": user.id, "skin_id": "classic"})
        if owned is None:
            session.add(db.UserOrbSkinModel(user_id=user.id, skin_id="classic"))
        selection = await session.get(db.UserOrbSkinSelectionModel, user.id)
        if selection is None:
            selection = db.UserOrbSkinSelectionModel(user_id=user.id, skin_id="classic")
            session.add(selection)
        await session.flush()
        # Also reconcile rewards if a mapping was added after an achievement was earned.
        await self._grant_achievement_orb_skins(session, user.id)
        await session.flush()
        owned_rows = (await session.execute(select(db.UserOrbSkinModel.skin_id).where(
            db.UserOrbSkinModel.user_id == user.id).order_by(db.UserOrbSkinModel.skin_id))).scalars().all()
        return sorted(set(owned_rows) | {"classic"}), selection.skin_id

    async def list_orb_skins(self, session: AsyncSession) -> list[OrbSkinSchema]:
        rows = (await session.execute(select(db.OrbSkinModel).where(db.OrbSkinModel.enabled.is_(True))
                                      .order_by(db.OrbSkinModel.id))).scalars().all()
        reward_rows = (
            await session.execute(
                select(db.AchievementOrbSkinRewardModel.skin_id, db.AchievementModel.code)
                .join(db.AchievementModel,
                      db.AchievementModel.id == db.AchievementOrbSkinRewardModel.achievement_id)
                .where(db.AchievementModel.enabled.is_(True))
                .order_by(db.AchievementOrbSkinRewardModel.skin_id, db.AchievementModel.code)
            )
        ).all()
        unlock_codes: dict[str, list[str]] = {}
        for skin_id, code in reward_rows:
            unlock_codes.setdefault(skin_id, []).append(code)
        base_url = str(get_settings().orb_skin_asset_base_url).rstrip("/")
        if not any(row.id == "classic" for row in rows):
            classic = self._orb_skin_schema(
                id="classic", name="Classic Orb", asset_key="classic",
                preview_asset_key="classic", asset_version="1", enabled=True,
                base_url=base_url,
            )
            return [classic, *[self._orb_skin_schema(
                id=row.id, name=row.name, asset_key=row.asset_key,
                preview_asset_key=row.preview_asset_key,
                asset_version=getattr(row, "asset_version", "1"), enabled=row.enabled,
                rarity=getattr(row, "rarity", "common"),
                price_gems=getattr(row, "price_gems", None),
                unlock_achievement_codes=unlock_codes.get(row.id, []),
                base_url=base_url,
            ) for row in rows]]
        return [self._orb_skin_schema(
            id=row.id, name=row.name, asset_key=row.asset_key,
            preview_asset_key=row.preview_asset_key,
            asset_version=getattr(row, "asset_version", "1"), enabled=row.enabled,
            rarity=getattr(row, "rarity", "common"),
            price_gems=getattr(row, "price_gems", None),
            unlock_achievement_codes=unlock_codes.get(row.id, []),
            base_url=base_url,
        ) for row in rows]

    @staticmethod
    def _orb_skin_schema(
        *,
        id: str,
        name: str,
        asset_key: str,
        preview_asset_key: str,
        asset_version: str,
        enabled: bool,
        base_url: str,
        rarity: str = "common",
        price_gems: int | None = None,
        unlock_achievement_codes: list[str] | None = None,
    ) -> OrbSkinSchema:
        skin_key = quote(asset_key, safe="")
        preview_key = quote(preview_asset_key, safe="")
        version = quote(asset_version or "1", safe="")
        version_query = f"?v={version}"
        asset_root = f"{base_url}/orb-skins/{skin_key}"
        preview_root = f"{base_url}/orb-skins/{preview_key}"
        return OrbSkinSchema(
            id=id,
            name=name,
            asset_key=asset_key,
            preview_asset_key=preview_asset_key,
            enabled=enabled,
            rarity=rarity if rarity in {"common", "rare", "epic", "legendary"} else "common",
            price_gems=price_gems,
            unlock_achievement_codes=unlock_achievement_codes or [],
            assets=OrbSkinAssetsSchema(
                version=asset_version or "1",
                preview_url=f"{preview_root}/preview.webp{version_query}",
                texture_url=f"{asset_root}/texture.webp{version_query}",
                detail_url=f"{asset_root}/detail.webp{version_query}",
                collision_sound=OrbSkinCollisionSoundSchema(
                    url=f"{asset_root}/collision.mp3{version_query}",
                    mime_type="audio/mpeg",
                ),
            ),
        )

    async def get_profile_by_username(
        self,
        session: AsyncSession,
        username: str,
    ) -> ProfileResponse:
        user = (
            await session.execute(
                select(db.UserModel).where(
                    func.lower(db.UserModel.username) == username.lower(),
                    db.UserModel.is_guest.is_(False),
                )
            )
        ).scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
        return await self.get_profile_by_user_id(session, user.id)

    async def update_profile(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        *,
        username: str | None,
        avatar_id: int | None,
        equipped_orb_skin_id: str | None = None,
        nationality: str | None = None,
    ) -> ProfileResponse:
        user = await session.get(db.UserModel, user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
        profile, _ = await self.ensure_records(session, user)
        owned_skins, _ = await self.ensure_orb_skin_records(session, user)

        if username is not None and username != user.username:
            if username.lower() != user.username.lower():
                existing = (
                    await session.execute(
                        select(db.UserModel.id).where(
                            func.lower(db.UserModel.username) == username.lower(),
                            db.UserModel.id != user.id,
                            db.UserModel.is_guest.is_(False),
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already taken")
            user.username = username

        if nationality is not None:
            if user.is_guest and nationality != "global":
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail='Guest profiles must use nationality "global"',
                )
            user.nationality = "global" if user.is_guest else nationality

        if avatar_id is not None:
            avatar = await session.get(db.AvatarModel, avatar_id)
            if avatar is None or not avatar.enabled:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Avatar is not available")
            profile.avatar_id = avatar_id

        if equipped_orb_skin_id is not None:
            skin = await session.get(db.OrbSkinModel, equipped_orb_skin_id)
            if skin is None or not skin.enabled or equipped_orb_skin_id not in owned_skins:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Orb skin is not available")
            selection = await session.get(db.UserOrbSkinSelectionModel, user.id)
            selection.skin_id = equipped_orb_skin_id

        profile.updated_at = datetime.now(UTC)
        await session.flush()
        return await self.get_profile_by_user_id(session, user.id)

    async def record_longest_chain(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        chain_length: int,
    ) -> None:
        if chain_length <= 0:
            return
        now = datetime.now(UTC)
        result = await session.execute(
            update(db.PlayerStatsModel)
            .where(db.PlayerStatsModel.user_id == user_id)
            .values(
                longest_chain=case(
                    (db.PlayerStatsModel.longest_chain < chain_length, chain_length),
                    else_=db.PlayerStatsModel.longest_chain,
                ),
                updated_at=now,
            )
        )
        if result.rowcount == 0:
            session.add(
                db.PlayerStatsModel(
                    user_id=user_id,
                    longest_chain=chain_length,
                    updated_at=now,
                )
            )

    async def apply_finished_game(
        self,
        session: AsyncSession,
        game: db.GameModel,
        participants: list[db.GameParticipantModel],
    ) -> bool:
        """Apply match counters exactly once while the caller holds the game lock."""
        if game.stats_applied_at is not None:
            return False
        if game.winner_id is None or game.loser_id is None:
            raise RuntimeError("Finished game is missing winner or loser")

        now = datetime.now(UTC)
        winner_new_streak = db.PlayerStatsModel.current_win_streak + 1
        winner_update = (
            update(db.PlayerStatsModel)
            .where(db.PlayerStatsModel.user_id == game.winner_id)
            .values(
                matches_played=db.PlayerStatsModel.matches_played + 1,
                matches_won=db.PlayerStatsModel.matches_won + 1,
                current_win_streak=winner_new_streak,
                longest_win_streak=case(
                    (db.PlayerStatsModel.longest_win_streak < winner_new_streak, winner_new_streak),
                    else_=db.PlayerStatsModel.longest_win_streak,
                ),
                updated_at=now,
            )
            .returning(db.PlayerStatsModel.current_win_streak)
        )
        winning_streak = (await session.execute(winner_update)).scalar_one_or_none()
        winner_stats_created = winning_streak is None
        if winning_streak is None:
            session.add(
                db.PlayerStatsModel(
                    user_id=game.winner_id,
                    matches_played=1,
                    matches_won=1,
                    current_win_streak=1,
                    longest_win_streak=1,
                    current_ranked_win_streak=1 if game.is_ranked else 0,
                    updated_at=now,
                )
            )
            winning_streak = 1

        ranked_winning_streak = None
        if game.is_ranked and not winner_stats_created:
            ranked_winning_streak = (await session.execute(
                update(db.PlayerStatsModel)
                .where(db.PlayerStatsModel.user_id == game.winner_id)
                .values(
                    current_ranked_win_streak=db.PlayerStatsModel.current_ranked_win_streak + 1,
                    updated_at=now,
                )
                .returning(db.PlayerStatsModel.current_ranked_win_streak)
            )).scalar_one()
        elif game.is_ranked:
            ranked_winning_streak = 1

        loser_update = await session.execute(
            update(db.PlayerStatsModel)
            .where(db.PlayerStatsModel.user_id == game.loser_id)
            .values(
                matches_played=db.PlayerStatsModel.matches_played + 1,
                current_win_streak=0,
                current_ranked_win_streak=(
                    0 if game.is_ranked else db.PlayerStatsModel.current_ranked_win_streak
                ),
                updated_at=now,
            )
        )
        if loser_update.rowcount == 0:
            session.add(
                db.PlayerStatsModel(
                    user_id=game.loser_id,
                    matches_played=1,
                    current_win_streak=0,
                    current_ranked_win_streak=0,
                    updated_at=now,
                )
            )

        for participant in participants:
            participant.result = "WIN" if participant.user_id == game.winner_id else "LOSS"

        if winning_streak >= 20:
            await self._award_achievement(
                session,
                game.winner_id,
                WIN_STREAK_ACHIEVEMENT,
                metadata={"winning_streak": winning_streak, "game_id": str(game.id)},
                earned_at=now,
            )

        if ranked_winning_streak is not None and ranked_winning_streak >= 20:
            await self._award_achievement(
                session,
                game.winner_id,
                RANKED_WIN_STREAK_ACHIEVEMENT,
                metadata={"ranked_winning_streak": ranked_winning_streak, "game_id": str(game.id)},
                earned_at=now,
            )

        await competitive_service.apply_finished_game(session, game, participants)
        game.stats_applied_at = now
        return True

    async def _award_achievement(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        code: str,
        *,
        metadata: dict,
        earned_at: datetime,
    ) -> None:
        achievement = (
            await session.execute(
                select(db.AchievementModel).where(
                    db.AchievementModel.code == code,
                    db.AchievementModel.enabled.is_(True),
                )
            )
        ).scalar_one_or_none()
        if achievement is None:
            return
        existing = await session.get(
            db.UserAchievementModel,
            {"user_id": user_id, "achievement_id": achievement.id},
        )
        if existing is None:
            session.add(
                db.UserAchievementModel(
                    user_id=user_id,
                    achievement_id=achievement.id,
                    earned_at=earned_at,
                    achievement_metadata=metadata,
                )
            )
            await self._grant_achievement_orb_skins(
                session,
                user_id,
                achievement_id=achievement.id,
            )

    @staticmethod
    async def _grant_achievement_orb_skins(
        session: AsyncSession,
        user_id: uuid.UUID,
        *,
        achievement_id: int | None = None,
    ) -> None:
        statement = (
            select(db.AchievementOrbSkinRewardModel.skin_id)
            .join(db.OrbSkinModel,
                  db.OrbSkinModel.id == db.AchievementOrbSkinRewardModel.skin_id)
            .join(db.UserAchievementModel,
                  db.UserAchievementModel.achievement_id == db.AchievementOrbSkinRewardModel.achievement_id)
            .where(db.UserAchievementModel.user_id == user_id, db.OrbSkinModel.enabled.is_(True))
        )
        if achievement_id is not None:
            statement = statement.where(db.UserAchievementModel.achievement_id == achievement_id)
        reward_skin_ids = (await session.execute(statement)).scalars().all()
        await grant_orb_skins(session, user_id, reward_skin_ids)

    @staticmethod
    def _avatar_schema(avatar: db.AvatarModel) -> AvatarSchema:
        return AvatarSchema(
            id=avatar.id,
            code=avatar.code,
            name=avatar.name,
            image_url=avatar.image_url,
        )

    def _profile_response(
        self,
        user: db.UserModel,
        avatar: db.AvatarModel,
        stats: db.PlayerStatsModel,
        achievements: list[AchievementSchema],
        owned_orb_skin_ids: list[str],
        equipped_orb_skin_id: str,
    ) -> ProfileResponse:
        created_at = user.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        age_days = max(0, (datetime.now(UTC) - created_at).days)
        win_rate = (
            round((stats.matches_won / stats.matches_played) * 100, 2)
            if stats.matches_played
            else 0.0
        )
        return ProfileResponse(
            user_id=user.id,
            username=user.username,
            nationality=getattr(user, "nationality", "global") or "global",
            avatar=self._avatar_schema(avatar),
            stats=PlayerStatsSchema(
                matches_played=stats.matches_played,
                matches_won=stats.matches_won,
                win_rate=win_rate,
                current_win_streak=stats.current_win_streak,
                longest_win_streak=stats.longest_win_streak,
                longest_chain=stats.longest_chain,
            ),
            achievements=achievements,
            created_at=created_at,
            profile_age_days=age_days,
            equipped_orb_skin_id=equipped_orb_skin_id,
            owned_orb_skin_ids=owned_orb_skin_ids,
        )


profile_service = ProfileService()
