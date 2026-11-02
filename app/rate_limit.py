from __future__ import annotations

import hashlib
import uuid

from fastapi import HTTPException, Request, status

from app.config.settings import get_settings
from app.redis.client import RedisClient
from app.redis.keys import rate_limit_key


_SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local redis_time = redis.call('TIME')
local now = tonumber(redis_time[1]) * 1000 + math.floor(tonumber(redis_time[2]) / 1000)
local cutoff = now - tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff)
local count = redis.call('ZCARD', key)
if count >= limit then return 0 end
redis.call('ZADD', key, now, ARGV[3])
redis.call('PEXPIRE', key, ARGV[1])
return 1
"""


async def enforce_rate_limit(
    *, user_id: str, scope: str, limit: int, window_seconds: int = 60
) -> None:
    try:
        allowed = await RedisClient.instance().eval(
            _SLIDING_WINDOW_SCRIPT,
            1,
            rate_limit_key(scope, user_id),
            window_seconds * 1000,
            limit,
            str(uuid.uuid4()),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Rate limiting is temporarily unavailable",
        ) from exc
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(window_seconds)},
        )


async def enforce_move_rate_limit(user_id: str) -> None:
    await enforce_rate_limit(
        user_id=user_id,
        scope="moves",
        limit=get_settings().rate_limit_moves_per_minute,
    )


async def enforce_auth_attempt_limit(request: Request | None, operation: str) -> None:
    """Throttle unauthenticated auth flows by the connection's client address.

    The API gateway/ASGI server must be configured to trust forwarded headers
    only from known proxies so ``request.client.host`` cannot be spoofed.
    ``None`` is supported for direct handler calls outside an HTTP request.
    """
    if request is None:
        return
    settings = get_settings()
    limits = {
        "login": settings.auth_login_attempts_per_minute,
        "google": settings.auth_login_attempts_per_minute,
        "register": settings.auth_registration_attempts_per_minute,
        "guest": settings.auth_guest_attempts_per_minute,
    }
    limit = limits.get(operation)
    if limit is None:
        raise ValueError(f"Unknown auth rate-limit operation: {operation}")
    client_host = request.client.host if request.client else "unknown"
    client_key = hashlib.sha256(client_host.encode("utf-8")).hexdigest()
    await enforce_rate_limit(
        user_id=client_key,
        scope=f"auth-{operation}",
        limit=limit,
    )


async def enforce_password_change_limit(user_id: str) -> None:
    await enforce_rate_limit(
        user_id=user_id,
        scope="auth-password-change",
        limit=get_settings().auth_password_changes_per_minute,
    )


async def enforce_websocket_message_rate_limit(user_id: str) -> None:
    await enforce_rate_limit(
        user_id=user_id,
        scope="websocket-messages",
        limit=get_settings().websocket_messages_per_minute,
    )


async def enforce_websocket_snapshot_rate_limit(user_id: str) -> None:
    await enforce_rate_limit(
        user_id=user_id,
        scope="websocket-snapshots",
        limit=get_settings().websocket_snapshots_per_minute,
    )


async def enforce_websocket_connect_rate_limit(user_id: str) -> None:
    await enforce_rate_limit(
        user_id=user_id,
        scope="websocket-connections",
        limit=get_settings().websocket_connections_per_minute,
    )


async def enforce_websocket_ticket_rate_limit(user_id: str) -> None:
    await enforce_rate_limit(
        user_id=user_id,
        scope="websocket-tickets",
        limit=get_settings().websocket_connections_per_minute,
    )
