from __future__ import annotations

import uuid
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import models as db

PLACEMENT_GAMES = 10
MAX_FLUX = Decimal("5.00")
FLUX_STEP = Decimal("0.50")
LEAGUE_NAMES = (
    "Technetium", "Promethium", "Polonium", "Radon", "Francium",
    "Radium", "Actinium", "Thorium", "Uranium", "Plutonium",
)


def calculate_flux(wins: int, losses: int) -> Decimal:
    if losses == 0:
        return MAX_FLUX
    return min(MAX_FLUX, (Decimal(wins) / Decimal(losses)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def flux_league(flux: Decimal) -> tuple[str, Decimal, Decimal]:
    index = min(int(flux / FLUX_STEP), len(LEAGUE_NAMES) - 1)
    return LEAGUE_NAMES[index], FLUX_STEP * index, FLUX_STEP * (index + 1)


class CompetitiveService:
    async def get_or_create_rating(
        self, session: AsyncSession, user_id: uuid.UUID, *, lock: bool = False
    ) -> db.CompetitiveRatingModel:
        # The primary key is the user ID. Insert-on-conflict makes the first
        # ranked requests for a new player safe when they arrive concurrently.
        await session.execute(
            pg_insert(db.CompetitiveRatingModel)
            .values(user_id=user_id, wins=0, losses=0, flux=0)
            .on_conflict_do_nothing(index_elements=[db.CompetitiveRatingModel.user_id])
        )
        stmt = select(db.CompetitiveRatingModel).where(db.CompetitiveRatingModel.user_id == user_id)
        if lock:
            stmt = stmt.with_for_update()
        rating = (await session.execute(stmt)).scalar_one_or_none()
        if rating is None:  # Defensive guard: insert/upsert should make this impossible.
            raise RuntimeError("Could not create competitive rating")
        return rating

    async def profile(self, session: AsyncSession, user_id: uuid.UUID) -> dict:
        rating = await self.get_or_create_rating(session, user_id)
        played = rating.wins + rating.losses
        is_ranked = played >= PLACEMENT_GAMES
        flux = Decimal(str(rating.flux)).quantize(Decimal("0.01")) if is_ranked else None
        league, lower, upper = flux_league(flux) if flux is not None else (None, None, None)
        return {
            "status": "ranked" if is_ranked else "unranked",
            "games_played": played,
            "placement_games_remaining": max(0, PLACEMENT_GAMES - played),
            "wins": rating.wins,
            "losses": rating.losses,
            "flux": float(flux) if flux is not None else None,
            "league": league,
            "league_flux_min": float(lower) if lower is not None else None,
            "league_flux_max": float(upper) if upper is not None else None,
        }

    async def apply_finished_game(
        self,
        session: AsyncSession,
        game: db.GameModel,
        participants: list[db.GameParticipantModel],
    ) -> None:
        if not game.is_ranked:
            return
        if game.winner_id is None or game.loser_id is None:
            raise RuntimeError("Ranked game is missing winner or loser")
        prior = await session.get(db.GameRatingChangeModel, {
            "game_id": game.id, "user_id": game.winner_id,
        })
        if prior is not None:
            return

        participant_ids = {p.user_id for p in participants}
        if {game.winner_id, game.loser_id} - participant_ids:
            raise RuntimeError("Ranked game participants do not match winner and loser")

        rows = (await session.execute(
            select(db.CompetitiveRatingModel)
            .where(db.CompetitiveRatingModel.user_id.in_([game.winner_id, game.loser_id]))
            .order_by(db.CompetitiveRatingModel.user_id)
            .with_for_update()
        )).scalars().all()
        by_user = {row.user_id: row for row in rows}
        for user_id in (game.winner_id, game.loser_id):
            if user_id not in by_user:
                by_user[user_id] = db.CompetitiveRatingModel(user_id=user_id, wins=0, losses=0, flux=0)
                session.add(by_user[user_id])
        await session.flush()

        for user_id, result in ((game.winner_id, "WIN"), (game.loser_id, "LOSS")):
            rating = by_user[user_id]
            games_before = rating.wins + rating.losses
            flux_before = calculate_flux(rating.wins, rating.losses) if games_before >= PLACEMENT_GAMES else None
            if result == "WIN":
                rating.wins += 1
            else:
                rating.losses += 1
            rating.flux = calculate_flux(rating.wins, rating.losses)
            games_after = rating.wins + rating.losses
            flux_after = calculate_flux(rating.wins, rating.losses) if games_after >= PLACEMENT_GAMES else None
            session.add(db.GameRatingChangeModel(
                game_id=game.id,
                user_id=user_id,
                flux_before=flux_before,
                flux_after=flux_after,
                wins_after=rating.wins,
                losses_after=rating.losses,
                result=result,
            ))
        await session.flush()


competitive_service = CompetitiveService()
