"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from graphbot import __version__
from graphbot.agent.runner import GraphRunner
from graphbot.api.admin import router as admin_router
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


# ── Rate Limiting Middleware ─────────────────────────────────


class RateLimitMiddleware(BaseHTTPMiddleware):
    """In-memory sliding window rate limiter (IP-based)."""

    # Paths exempt from rate limiting
    _EXEMPT = frozenset({"/health", "/docs", "/openapi.json", "/redoc"})

    def __init__(self, app: FastAPI) -> None:
        super().__init__(app)
        self._requests: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        # Skip if no config loaded yet or rate limiting disabled
        config = getattr(request.app.state, "config", None)
        if not config or not config.auth.rate_limit.enabled:
            return await call_next(request)

        # Exempt paths
        path = request.url.path
        if path in self._EXEMPT or path.startswith("/webhook"):
            return await call_next(request)

        rpm = config.auth.rate_limit.requests_per_minute
        ip = request.client.host if request.client else "unknown"
        now = time.time()
        window = 60.0

        # Clean old entries
        self._requests[ip] = [t for t in self._requests[ip] if now - t < window]

        if len(self._requests[ip]) >= rpm:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests"},
                headers={"Retry-After": "60"},
            )

        self._requests[ip].append(now)
        return await call_next(request)


# ── App Factory ──────────────────────────────────────────────


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
    from graphbot.agent.delegation import DelegationPlanner
    from graphbot.agent.tools import make_tools
    from graphbot.agent.tools.registry import (
        build_background_tool_registry,
        get_tool_catalog,
    )

    config = load_config()
    db = MemoryStore(str(config.db_path))
    _ensure_owner(config, db)

    # Create runner with empty tools first (chicken-and-egg: scheduler needs runner)
    runner = GraphRunner(config, db, tools=[])

    # Background services (need runner)
    cron_scheduler = CronScheduler(db, runner, config=config)
    heartbeat = HeartbeatService(config, runner)
    worker = SubagentWorker(config, db=db)

    # Delegation planner — plans subagent execution via LLM
    bg_registry = build_background_tool_registry(config, db)
    tool_catalog = get_tool_catalog(bg_registry)
    planner = DelegationPlanner(config, tool_catalog)

    # Now build tools with scheduler+worker+planner, and rebuild graph
    from graphbot.agent.graph import create_graph

    tools = make_tools(config, db, scheduler=cron_scheduler, worker=worker, planner=planner)
    runner.tools = tools
    runner._graph = create_graph(config, db, tools)

    await cron_scheduler.start()
    heartbeat_task = asyncio.create_task(heartbeat.start())

    # WebSocket connection registry for event push
    from graphbot.api.ws import ConnectionManager

    ws_manager = ConnectionManager()
    cron_scheduler.ws_manager = ws_manager
    worker.ws_manager = ws_manager

    app.state.config = config
    app.state.db = db
    app.state.runner = runner
    app.state.cron = cron_scheduler
    app.state.heartbeat = heartbeat
    app.state.worker = worker
    app.state.ws_manager = ws_manager

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
        version=__version__,
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
    app.add_middleware(RateLimitMiddleware)

    # Routers
    app.include_router(core_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(ws_router)

    # Channel webhooks
    app.include_router(telegram_router)
    app.include_router(discord_router)
    app.include_router(whatsapp_router)
    app.include_router(feishu_router)

    return app


app = create_app()
