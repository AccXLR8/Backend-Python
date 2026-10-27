from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import models as db
from app.games.models import GameStatus


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create(self, user_id: uuid.UUID, username: str, **extra) -> db.UserModel:
        result = await self.session.execute(select(db.UserModel).where(db.UserModel.id == user_id))
        user = result.scalar_one_or_none()
        if user:
            return user
        user = db.UserModel(id=user_id, username=username, **extra)
        self.session.add(user)
        await self.session.flush()
        return user

    async def get_by_username(self, username: str) -> db.UserModel | None:
        result = await self.session.execute(select(db.UserModel).where(db.UserModel.username == username))
        return result.scalar_one_or_none()

    async def get_registered_by_username(self, username: str) -> db.UserModel | None:
        result = await self.session.execute(
            select(db.UserModel).where(db.UserModel.username == username, db.UserModel.is_guest.is_(False))
        )
        return result.scalar_one_or_none()

    async def create(self, **kwargs) -> db.UserModel:
        user = db.UserModel(**kwargs)
        self.session.add(user)
        await self.session.flush()
        return user

    async def get(self, user_id: uuid.UUID) -> db.UserModel | None:
        result = await self.session.execute(select(db.UserModel).where(db.UserModel.id == user_id))
        return result.scalar_one_or_none()


class GameRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, game: db.GameModel) -> db.GameModel:
        self.session.add(game)
        await self.session.flush()
        return game

    async def get(self, game_id: uuid.UUID) -> db.GameModel | None:
        result = await self.session.execute(select(db.GameModel).where(db.GameModel.id == game_id))
        return result.scalar_one_or_none()

    async def get_with_participants_for_update(
        self, game_id: uuid.UUID
    ) -> tuple[db.GameModel | None, list[db.GameParticipantModel]]:
        """Load and lock one game plus its participants in a single round trip."""
        stmt = (
            select(db.GameModel, db.GameParticipantModel)
            .outerjoin(
                db.GameParticipantModel,
                db.GameParticipantModel.game_id == db.GameModel.id,
            )
            .where(db.GameModel.id == game_id)
            .order_by(db.GameParticipantModel.player_slot)
            .with_for_update(of=db.GameModel)
        )
        rows = (await self.session.execute(stmt)).all()
        if not rows:
            return None, []
        game = rows[0][0]
        participants = [participant for _, participant in rows if participant is not None]
        return game, participants

    async def list_games_for_user(self, user_id: uuid.UUID) -> Sequence[db.GameModel]:
        stmt = select(db.GameModel).where((db.GameModel.player_1_id == user_id) | (db.GameModel.player_2_id == user_id))
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def update_state(self, game_id: uuid.UUID, **kwargs) -> None:
        await self.session.execute(update(db.GameModel).where(db.GameModel.id == game_id).values(**kwargs))


class ParticipantRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, participant: db.GameParticipantModel) -> db.GameParticipantModel:
        self.session.add(participant)
        await self.session.flush()
        return participant

    async def get(self, game_id: uuid.UUID, user_id: uuid.UUID) -> db.GameParticipantModel | None:
        result = await self.session.execute(
            select(db.GameParticipantModel).where(
                db.GameParticipantModel.game_id == game_id,
                db.GameParticipantModel.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_game(self, game_id: uuid.UUID) -> Sequence[db.GameParticipantModel]:
        result = await self.session.execute(
            select(db.GameParticipantModel).where(db.GameParticipantModel.game_id == game_id).order_by(db.GameParticipantModel.player_slot)
        )
        return result.scalars().all()


class MoveRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, move: db.MoveModel, *, flush: bool = True) -> db.MoveModel:
        self.session.add(move)
        if flush:
            await self.session.flush()
        return move

    async def last_sequence(self, game_id: uuid.UUID) -> int:
        result = await self.session.execute(
            select(db.MoveModel.sequence).where(db.MoveModel.game_id == game_id).order_by(db.MoveModel.sequence.desc()).limit(1)
        )
        row = result.scalar_one_or_none()
        return row or 0

    async def list_for_game(self, game_id: uuid.UUID) -> Sequence[db.MoveModel]:
        result = await self.session.execute(
            select(db.MoveModel).where(db.MoveModel.game_id == game_id).order_by(db.MoveModel.sequence)
        )
        return result.scalars().all()

    async def by_client_move_id(self, game_id: uuid.UUID, client_move_id: str) -> db.MoveModel | None:
        result = await self.session.execute(
            select(db.MoveModel).where(
                db.MoveModel.game_id == game_id,
                db.MoveModel.client_move_id == client_move_id,
            )
        )
        return result.scalar_one_or_none()


class GameEventRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def append(self, event: db.GameEventModel, *, flush: bool = True) -> db.GameEventModel:
        self.session.add(event)
        if flush:
            await self.session.flush()
        return event

    async def list_events(self, game_id: uuid.UUID) -> Sequence[db.GameEventModel]:
        result = await self.session.execute(select(db.GameEventModel).where(db.GameEventModel.game_id == game_id).order_by(db.GameEventModel.sequence))
        return result.scalars().all()
