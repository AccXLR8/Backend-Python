from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

from redis.asyncio import Redis

logger = logging.getLogger("app.redis")


class RedisClient:
    _client: Redis | None = None

    @classmethod
    async def connect(cls, url: str) -> None:
        if cls._client is not None:
            return
        safe_location = cls._safe_location(url)
        logger.info("Connecting to Redis (%s)...", safe_location)
        start = time.perf_counter()
        cls._client = Redis.from_url(url)
        try:
            await cls._client.ping()
        except Exception:
            elapsed = time.perf_counter() - start
            logger.exception("Redis connection FAILED after %.2fs", elapsed)
            if cls._client:
                await cls._client.close()
            cls._client = None
            raise
        else:
            elapsed = time.perf_counter() - start
            logger.info("Redis connected (%s) in %.2fs", safe_location, elapsed)

    @classmethod
    async def disconnect(cls) -> None:
        if cls._client is not None:
            logger.info("Disconnecting Redis client")
            await cls._client.close()
            cls._client = None
            logger.info("Redis client disconnected")

    @staticmethod
    def _safe_location(url: str) -> str:
        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port
        scheme = parsed.scheme or "redis"
        if port:
            return f"{scheme}://{host}:{port}"
        return f"{scheme}://{host}"

    @classmethod
    def instance(cls) -> Redis:
        if cls._client is None:
            raise RuntimeError("Redis not initialized")
        return cls._client

    @classmethod
    def is_connected(cls) -> bool:
        return cls._client is not None
