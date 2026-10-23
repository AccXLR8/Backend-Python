from __future__ import annotations

import logging
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.config.settings import get_settings

logger = logging.getLogger("app.migrations")


def _sync_url(url: str) -> str:
    sa_url = make_url(url)
    driver = sa_url.drivername
    if driver.startswith("postgresql+asyncpg"):
        driver = driver.replace("+asyncpg", "", 1)
    elif driver.startswith("postgresql+psycopg"):
        driver = driver.replace("+psycopg", "", 1)

    query = dict(sa_url.query)
    ssl_value = query.pop("ssl", None)
    if ssl_value and "sslmode" not in query:
        query["sslmode"] = ssl_value
    query.pop("channel_binding", None)

    sa_url = sa_url.set(drivername=driver, query=query)
    return sa_url.render_as_string(hide_password=False)


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
