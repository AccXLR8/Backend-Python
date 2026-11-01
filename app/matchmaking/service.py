from __future__ import annotations

import asyncio
import uuid

from app.matchmaking.modes import MatchmakingMode
from app.matchmaking.queue import MatchmakingQueue, RankedMatchmakingQueue, assign_pair, release_pair
from app.redis.client import RedisClient
from app.redis.keys import (
    legacy_matchmaking_assignment_key,
    matchmaking_assignment_key,
    matchmaking_claim_key,
    matchmaking_owner_key,
)


class MatchmakingService:
    def __init__(self):
        self._redis_factory = RedisClient.instance

    def _queue(self, mode: MatchmakingMode = MatchmakingMode.STANDARD) -> MatchmakingQueue:
        return MatchmakingQueue(self._redis_factory(), mode=mode)

    def _ranked_queue(self) -> RankedMatchmakingQueue:
        return RankedMatchmakingQueue(self._redis_factory())

    async def join_queue(
        self, user_id: uuid.UUID, mode: MatchmakingMode = MatchmakingMode.STANDARD
    ) -> tuple[str, str, str] | None:
        return await self._queue(mode).join_and_match(user_id)

    async def leave_queue(
        self, user_id: uuid.UUID, mode: MatchmakingMode = MatchmakingMode.STANDARD
    ) -> tuple[str, str | None]:
        state, value = await self._queue(mode).leave_or_get_claim(user_id)
        if state != "claimed":
            return state, value

        # A claim means matchmaking won the atomic race with cancellation. Give
        # the game-creation transaction a short window to publish its assignment.
        deadline = asyncio.get_running_loop().time() + 2.0
        while asyncio.get_running_loop().time() < deadline:
            assignment = await self.get_assignment(user_id, mode.value)
            if assignment:
                return "matched", assignment
            if not await self.is_claimed(user_id):
                break
            await asyncio.sleep(0.05)
        assignment = await self.get_assignment(user_id, mode.value)
        if assignment:
            return "matched", assignment
        if not await self.is_claimed(user_id):
            state, value = await self._queue(mode).leave_or_get_claim(user_id)
            if state != "claimed":
                return state, value
        return "matching", None

    async def save_pair_assignment(
        self,
        user_ids: list[uuid.UUID],
        game_id: uuid.UUID,
        mode: str,
        claim: str,
        ttl_seconds: int = 600,
    ) -> bool:
        return await assign_pair(
            self._redis_factory(),
            mode,
            (str(user_ids[0]), str(user_ids[1])),
            game_id,
            claim,
            ttl_seconds,
        )

    async def get_assignment(self, user_id: uuid.UUID, mode: str | None = None) -> str | None:
        value = await self.get_assignment_value(user_id)
        if not value:
            value = await self._redis_factory().get(legacy_matchmaking_assignment_key(str(user_id)))
            if isinstance(value, bytes):
                value = value.decode()
        if not value:
            return None
        if "|" in value:
            assignment_mode, game_id = value.split("|", 1)
            if mode is not None and assignment_mode != mode:
                return None
            return game_id
        # Read assignments created by older app versions. They have no mode;
        # only unscoped callers can safely consume them.
        return value if mode is None else None

    async def get_assignment_value(self, user_id: uuid.UUID) -> str | None:
        value = await self._redis_factory().get(matchmaking_assignment_key(str(user_id)))
        return value.decode() if isinstance(value, bytes) else value

    async def clear_assignment_if_value(self, user_id: uuid.UUID, value: str) -> bool:
        script = """
        if redis.call('GET', KEYS[1]) ~= ARGV[1] or redis.call('GET', KEYS[2]) then return 0 end
        redis.call('DEL', KEYS[1])
        local owner = redis.call('GET', KEYS[3]) or ''
        if string.sub(owner, 1, 9) == 'assigned|' then redis.call('DEL', KEYS[3]) end
        return 1
        """
        return bool(await self._redis_factory().eval(
            script,
            3,
            matchmaking_assignment_key(str(user_id)),
            matchmaking_claim_key(str(user_id)),
            matchmaking_owner_key(str(user_id)),
            value,
        ))

    async def is_claimed(self, user_id: uuid.UUID) -> bool:
        return bool(await self._redis_factory().exists(matchmaking_claim_key(str(user_id))))

    async def release_pair(
        self,
        pair: tuple[str, str],
        mode: str,
        claim: str,
        blocked_user_ids: set[str] | None = None,
    ) -> bool:
        return await release_pair(self._redis_factory(), mode, pair, claim, blocked_user_ids)

    async def join_ranked(
        self, user_id: uuid.UUID, flux: float | None
    ) -> tuple[str, str, str] | None:
        return await self._ranked_queue().join_and_match(user_id, flux)

    async def leave_ranked(self, user_id: uuid.UUID) -> tuple[str, str | None]:
        state, value = await self._ranked_queue().leave_or_get_state(user_id)
        if state != "claimed":
            return state, value
        deadline = asyncio.get_running_loop().time() + 2.0
        while asyncio.get_running_loop().time() < deadline:
            assignment = await self.get_assignment(user_id, "ranked")
            if assignment:
                return "matched", assignment
            if not await self.is_claimed(user_id):
                break
            await asyncio.sleep(0.05)
        assignment = await self.get_assignment(user_id, "ranked")
        if assignment:
            return "matched", assignment
        if not await self.is_claimed(user_id):
            state, value = await self._ranked_queue().leave_or_get_state(user_id)
            if state != "claimed":
                return state, value
        return "matching", None

    async def matchmaking_status(
        self, user_id: uuid.UUID, mode: MatchmakingMode
    ) -> str:
        assignment = await self.get_assignment(user_id, mode.value)
        if assignment:
            return "matched"
        redis = self._redis_factory()
        owner_key = matchmaking_owner_key(str(user_id))
        owner = await redis.get(owner_key)
        if isinstance(owner, bytes):
            owner = owner.decode()
        if owner == f"claimed|{mode.value}":
            if await self.is_claimed(user_id):
                return "queued"
            await self._queue(mode).repair_expired_claim(user_id)
            owner = await redis.get(owner_key)
            if isinstance(owner, bytes):
                owner = owner.decode()
        if owner == mode.value:
            await self._queue(mode).refresh_queue_lease(user_id)
            return "queued"
        return "idle"

    async def ranked_queue_status(self, user_id: uuid.UUID) -> str:
        assignment = await self.get_assignment(user_id, "ranked")
        if assignment:
            return "matched"
        redis = self._redis_factory()
        owner_key = matchmaking_owner_key(str(user_id))
        owner = await redis.get(owner_key)
        if isinstance(owner, bytes):
            owner = owner.decode()
        if owner == "claimed|ranked":
            if await self.is_claimed(user_id):
                return "queued"
            await self._ranked_queue().repair_expired_claim(user_id)
            owner = await redis.get(owner_key)
            if isinstance(owner, bytes):
                owner = owner.decode()
        if owner == "ranked":
            await self._ranked_queue().refresh_queue_lease(user_id)
            return "queued"
        return "idle"


matchmaking_service = MatchmakingService()
