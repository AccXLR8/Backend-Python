from __future__ import annotations

import ssl

from sqlalchemy.engine import make_url

from app.database.session import Database
from app.database.migrations_runner import _sync_url


def test_prepare_async_url_with_ssl_param_and_channel_binding_removed():
    original = "postgresql+asyncpg://user:pass@db.neon.tech/dbname?ssl=require&channel_binding=require"
    prepared, connect_args, metadata = Database._prepare_async_url(original)

    assert metadata["ssl"] is True
    assert isinstance(connect_args.get("ssl"), ssl.SSLContext)

    parsed = make_url(prepared)
    assert parsed.drivername == "postgresql+asyncpg"
    assert parsed.query == {}


def test_prepare_async_url_accepts_sslmode_parameter():
    original = "postgresql+asyncpg://user:pass@db.neon.tech/dbname?sslmode=require"
    prepared, connect_args, metadata = Database._prepare_async_url(original)

    assert metadata["ssl"] is True
    assert "ssl" in connect_args
    assert "ssl" not in make_url(prepared).query


def test_prepare_async_url_preserves_special_characters_in_password():
    original = "postgresql+asyncpg://user:p%40ss%3F@db.neon.tech/dbname?ssl=require"
    prepared, connect_args, metadata = Database._prepare_async_url(original)

    parsed = make_url(prepared)
    assert parsed.password == "p@ss?"
    assert metadata["ssl"] is True


def test_sync_url_converts_asyncpg_driver_and_ssl_flag():
    original = "postgresql+asyncpg://user:pass@db.neon.tech/dbname?ssl=require&channel_binding=require"
    sync_url = _sync_url(original)
    parsed = make_url(sync_url)

    assert parsed.drivername == "postgresql"
    assert parsed.query.get("sslmode") == "require"
    assert "ssl" not in parsed.query
    assert "channel_binding" not in parsed.query
