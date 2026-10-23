from __future__ import annotations

import logging
import ssl
import time
from typing import AsyncGenerator, Dict, Tuple

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.engine.url import URL as SAURL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logger = logging.getLogger("app.database")


class Database:
    _engine = None
    _session_factory: async_sessionmaker[AsyncSession] | None = None

    @classmethod
    async def connect(cls, database_url: str) -> None:
        if cls._engine is not None:
            return
        prepared_url, connect_args, metadata = cls._prepare_async_url(database_url)
        logger.info(
            "Database engine initialization (driver=%s, host=%s, ssl=%s)",
            metadata["driver"],
            metadata["host"],
            "enabled" if metadata["ssl"] else "disabled",
        )
        cls._engine = create_async_engine(prepared_url, echo=False, future=True, connect_args=connect_args)
        cls._session_factory = async_sessionmaker(cls._engine, expire_on_commit=False)

    @classmethod
    async def verify_connection(cls) -> None:
        if cls._engine is None:
            raise RuntimeError("Database engine has not been initialized")
        start = time.perf_counter()
        try:
            async with cls._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception:
            elapsed = time.perf_counter() - start
            logger.exception("Database connectivity check FAILED after %.2fs", elapsed)
            raise
        else:
            elapsed = time.perf_counter() - start
            logger.info("Database connectivity check succeeded in %.2fs", elapsed)

    @classmethod
    async def disconnect(cls) -> None:
        if cls._engine is not None:
            logger.info("Disposing database engine")
            await cls._engine.dispose()
            cls._engine = None
            cls._session_factory = None
            logger.info("Database engine disposed")

    @classmethod
    def is_connected(cls) -> bool:
        return cls._engine is not None

    @classmethod
    def session_factory(cls) -> async_sessionmaker[AsyncSession]:
        if cls._session_factory is None:
            raise RuntimeError("Database has not been initialized")
        return cls._session_factory

    @staticmethod
    def _prepare_async_url(database_url: str) -> Tuple[str, Dict[str, object], Dict[str, object]]:
        url: SAURL = make_url(database_url)
        metadata = {
            "driver": url.drivername,
            "host": url.host or "local",
            "ssl": False,
        }
        connect_args: Dict[str, object] = {}
        query = dict(url.query)

        if url.drivername.startswith("postgresql+asyncpg"):
            ssl_setting = (
                (query.pop("ssl", None) or query.pop("sslmode", None) or "").lower()
            )
            # Remove unsupported asyncpg arguments
            query.pop("channel_binding", None)

            if ssl_setting in {"require", "verify-ca", "verify-full"}:
                connect_args["ssl"] = ssl.create_default_context()
                metadata["ssl"] = True
            elif ssl_setting in {"", "disable", "allow"}:
                metadata["ssl"] = False
            elif ssl_setting:
                logger.warning("Unknown SSL option '%s' - assuming TLS required", ssl_setting)
                connect_args["ssl"] = ssl.create_default_context()
                metadata["ssl"] = True

            url = url.set(query=query)

        return url.render_as_string(hide_password=False), connect_args, metadata


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    session_factory = Database.session_factory()
    async with session_factory() as session:
        yield session
