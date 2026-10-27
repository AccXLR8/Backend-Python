from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import main


@pytest.mark.asyncio
async def test_startup_failure_cleans_up_initialized_database(monkeypatch):
    settings = SimpleNamespace(
        app_env="test",
        database_url="sqlite+aiosqlite:///unused.db",
        redis_url="redis://unavailable:6379/0",
    )
    database_connect = AsyncMock()
    database_verify = AsyncMock()
    database_disconnect = AsyncMock()
    redis_connect = AsyncMock(side_effect=ConnectionError("redis unavailable"))

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main.Database, "connect", database_connect)
    monkeypatch.setattr(main.Database, "verify_connection", database_verify)
    monkeypatch.setattr(main.Database, "is_connected", lambda: True)
    monkeypatch.setattr(main.Database, "disconnect", database_disconnect)
    monkeypatch.setattr(main, "run_migrations_once", lambda: None)
    monkeypatch.setattr(main.RedisClient, "connect", redis_connect)
    monkeypatch.setattr(main.RedisClient, "is_connected", lambda: False)

    with pytest.raises(ConnectionError, match="redis unavailable"):
        async with main.lifespan(SimpleNamespace()):
            pass

    database_connect.assert_awaited_once_with(settings.database_url)
    database_verify.assert_awaited_once()
    redis_connect.assert_awaited_once_with(settings.redis_url)
    database_disconnect.assert_awaited_once()
