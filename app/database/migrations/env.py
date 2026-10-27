from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig
from pathlib import Path
from typing import Any

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from app.database import models

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    # Preserve loggers created by Uvicorn/FastAPI when migrations run inside
    # application startup. CLI migrations still receive the Alembic formatter.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata

target_metadata = models.Base.metadata


def _get_url() -> str:
    # Programmatic startup supplies an already normalized synchronous URL.
    # The environment/config fallbacks remain for the Alembic CLI.
    url = (
        config.attributes.get("database_url")
        or os.getenv("DATABASE_URL")
        or config.get_main_option("sqlalchemy.url")
    )
    if url.startswith("postgresql+asyncpg"):
        return url.replace("+asyncpg", "", 1)
    if url.startswith("postgresql+psycopg"):  # psycopg3 async DSN
        return url.replace("+psycopg", "", 1)
    return url


def run_migrations_offline() -> None:
    url = _get_url()
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        do_run_migrations(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
