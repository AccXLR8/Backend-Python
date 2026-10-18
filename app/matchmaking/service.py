from __future__ import annotations

import uuid

from app.matchmaking.modes import MatchmakingMode
from app.redis.client import RedisClient
from app.redis.keys import matchmaking_assignment_key
from app.matchmaking.queue import MatchmakingQueue


class MatchmakingService:
    def __init__(self):
        self._redis_factory = RedisClient.instance

    def _queue(self, mode: MatchmakingMode = MatchmakingMode.STANDARD) -> MatchmakingQueue:
        return MatchmakingQueue(self._redis_factory(), mode=mode)

    async def join_queue(self, user_id: uuid.UUID, mode: MatchmakingMode = MatchmakingMode.STANDARD) -> tuple[str, str] | None:
        # Ensure a player is in only one queue at a time.
        for other_mode in MatchmakingMode:
            if other_mode != mode:
                await self._queue(other_mode).remove(user_id)
        queue = self._queue(mode)
        await queue.enqueue(user_id)
        return await queue.try_match()

    async def leave_queue(self, user_id: uuid.UUID, mode: MatchmakingMode = MatchmakingMode.STANDARD) -> None:
        queue = self._queue(mode)
        await queue.remove(user_id)

    async def poll_pair(self, mode: MatchmakingMode = MatchmakingMode.STANDARD) -> tuple[str, str] | None:
        queue = self._queue(mode)
        return await queue.try_match()

    async def save_assignment(self, user_id: uuid.UUID, game_id: uuid.UUID, ttl_seconds: int = 600) -> None:
        client = self._redis_factory()
        await client.set(matchmaking_assignment_key(str(user_id)), str(game_id), ex=ttl_seconds)

    async def pop_assignment(self, user_id: uuid.UUID) -> str | None:
        client = self._redis_factory()
        key = matchmaking_assignment_key(str(user_id))
        value = await client.get(key)
        if value is None:
            return None
        await client.delete(key)
        return value.decode() if isinstance(value, bytes) else value


matchmaking_service = MatchmakingService()
