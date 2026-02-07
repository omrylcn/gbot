"""FastAPI application factory."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from graphbot.agent.runner import GraphRunner
from graphbot.api.auth import router as auth_router
from graphbot.api.routes import router as core_router
from graphbot.api.ws import router as ws_router
from graphbot.core.background.heartbeat import HeartbeatService
from graphbot.core.background.worker import SubagentWorker
from graphbot.core.channels.discord import router as discord_router
from graphbot.core.channels.feishu import router as feishu_router
from graphbot.core.channels.telegram import router as telegram_router
from graphbot.core.channels.whatsapp import router as whatsapp_router
from graphbot.core.config.loader import load_config
from graphbot.core.cron.scheduler import CronScheduler
from graphbot.memory.store import MemoryStore


def _ensure_owner(config, db) -> None:
    """Create owner user in DB at startup if configured."""
    if config.assistant.owner is None:
        return
    owner = config.assistant.owner
    db.get_or_create_user(owner.username, name=owner.name)
    logger.info(f"Owner user ensured: {owner.username}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init Config → MemoryStore → GraphRunner → Background Services. Shutdown: cleanup."""
    from graphbot.agent.tools import make_tools

    config = load_config()
    db = MemoryStore(str(config.db_path))
    _ensure_owner(config, db)

    # Create runner with empty tools first (chicken-and-egg: scheduler needs runner)
    runner = GraphRunner(config, db, tools=[])

    # Background services (need runner)
    cron_scheduler = CronScheduler(db, runner, config=config)
    heartbeat = HeartbeatService(config, runner)
    worker = SubagentWorker(runner)

    # Now build tools with scheduler+worker, and rebuild graph
    from graphbot.agent.graph import create_graph

    tools = make_tools(config, db, scheduler=cron_scheduler, worker=worker)
    runner.tools = tools
    runner._graph = create_graph(config, db, tools)

    await cron_scheduler.start()
    heartbeat_task = asyncio.create_task(heartbeat.start())

    app.state.config = config
    app.state.db = db
    app.state.runner = runner
    app.state.cron = cron_scheduler
    app.state.heartbeat = heartbeat
    app.state.worker = worker

    logger.info(f"GraphBot API started — model: {config.assistant.model}")
    yield

    # Shutdown
    heartbeat.stop()
    heartbeat_task.cancel()
    await cron_scheduler.stop()
    await worker.shutdown()
    logger.info("GraphBot API shutting down")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="GraphBot API",
        description="General-purpose AI assistant API",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routers
    app.include_router(core_router)
    app.include_router(auth_router)
    app.include_router(ws_router)

    # Channel webhooks
    app.include_router(telegram_router)
    app.include_router(discord_router)
    app.include_router(whatsapp_router)
    app.include_router(feishu_router)

    return app


app = create_app()
