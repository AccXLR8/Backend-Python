from __future__ import annotations

import inspect
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.logging_config import configure_logging

configure_logging()

from app.config.settings import get_settings
from app.middleware.request_id import RequestIDMiddleware
from app.middleware.logging import LoggingMiddleware
from app.api.router import api_router
from app.redis.client import RedisClient
from app.database.session import Database
from app.database.migrations_runner import run_migrations_once

logger = logging.getLogger("app.startup")
STARTUP_STAGE_COUNT = 5


@asynccontextmanager
async def lifespan(app: FastAPI):
    resources = {"db": False, "redis": False}
    startup_complete = False

    settings = await _run_stage(1, "Loading configuration", get_settings)
    logger.info("Configuration loaded for env='%s'", settings.app_env)

    await _run_stage(2, "Connecting to PostgreSQL", lambda: Database.connect(settings.database_url))
    resources["db"] = True

    await _run_stage(3, "Verifying PostgreSQL connectivity", Database.verify_connection)

    await _run_stage(4, "Running database migrations", run_migrations_once)

    await _run_stage(5, "Connecting to Redis", lambda: RedisClient.connect(settings.redis_url))
    resources["redis"] = True

    logger.info("STARTUP COMPLETE - application is ready to serve requests")
    startup_complete = True
    try:
        yield
    except Exception:
        if startup_complete:
            logger.exception("Unhandled exception during application runtime")
        else:
            logger.error("Application startup failed (see previous logs)")
        raise
    finally:
        await _shutdown_resources(resources)
        logger.info("Application shutdown complete")


async def _run_stage(stage_number: int, description: str, func: Callable[[], Any]) -> Any:
    logger.info("STARTUP [%d/%d] %s...", stage_number, STARTUP_STAGE_COUNT, description)
    start = time.perf_counter()
    try:
        result = func()
        if inspect.isawaitable(result):
            result = await result
    except Exception:
        elapsed = time.perf_counter() - start
        logger.exception(
            "STARTUP [%d/%d] %s FAILED after %.2fs",
            stage_number,
            STARTUP_STAGE_COUNT,
            description,
            elapsed,
        )
        raise
    else:
        elapsed = time.perf_counter() - start
        logger.info(
            "STARTUP [%d/%d] %s completed in %.2fs",
            stage_number,
            STARTUP_STAGE_COUNT,
            description,
            elapsed,
        )
        return result


async def _shutdown_resources(resources: dict[str, bool]) -> None:
    if resources.get("redis") and RedisClient.is_connected():
        try:
            logger.info("Shutdown: disconnecting Redis")
            await RedisClient.disconnect()
        except Exception:
            logger.exception("Shutdown: failed to disconnect Redis")
    if resources.get("db") and Database.is_connected():
        try:
            logger.info("Shutdown: disposing database engine")
            await Database.disconnect()
        except Exception:
            logger.exception("Shutdown: failed to dispose database engine")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Chain Reaction Backend",
        version="0.1.0",
        debug=settings.debug,
        lifespan=lifespan,
    )

    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(LoggingMiddleware)

    # CORS
    allowed_origins = [
        "https://chain-reaction-frontend-ebon.vercel.app",
        "https://chain-reaction-frontend-qx3wq7pkq-samyak-sharmas-projects.vercel.app",
        "https://superkritical.com",
        "https://www.superkritical.com",
        "http://localhost:3000",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_origin_regex=r"https://([a-z0-9-]+\.)?superkritical\.com",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix="/api")

    from app.websocket import routes as ws_routes

    app.include_router(ws_routes.router)

    return app


app = create_app()
