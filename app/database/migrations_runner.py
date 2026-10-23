from __future__ import annotations

import logging
import time
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config.settings import get_settings

logger = logging.getLogger("app.migrations")


def _sync_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg"):
        return url.replace("+asyncpg", "", 1)
    if url.startswith("postgresql+psycopg"):
        return url.replace("+psycopg", "", 1)
    return url


_MIGRATIONS_RAN = False


def run_migrations_once() -> None:
    global _MIGRATIONS_RAN
    if _MIGRATIONS_RAN:
        return

    settings = get_settings()
    config_path = Path(__file__).resolve().parents[2] / "alembic.ini"
    alembic_cfg = Config(str(config_path))
    alembic_cfg.set_main_option("script_location", "app/database/migrations")
    alembic_cfg.set_main_option("sqlalchemy.url", _sync_url(settings.database_url))

    start = time.perf_counter()
    logger.info("Running Alembic migrations (target=head)...")
    try:
        command.upgrade(alembic_cfg, "head")
    except Exception:
        elapsed = time.perf_counter() - start
        logger.exception("Alembic migrations FAILED after %.2fs", elapsed)
        raise
    else:
        elapsed = time.perf_counter() - start
        logger.info("Alembic migrations finished in %.2fs", elapsed)
        _MIGRATIONS_RAN = True
