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

from gbot import __version__
from gbot.agent.runner import GraphRunner
from gbot.api.admin import router as admin_router
from gbot.api.auth import router as auth_router
from gbot.api.routes import router as core_router
from gbot.api.ws import router as ws_router
from gbot.core.background.heartbeat import HeartbeatService
from gbot.core.background.worker import SubagentWorker
from gbot.core.channels.discord import router as discord_router
from gbot.core.channels.feishu import router as feishu_router
from gbot.core.channels.telegram import router as telegram_router
from gbot.core.channels.whatsapp import router as whatsapp_router
from gbot.core.config.loader import load_config
from gbot.core.cron.scheduler import CronScheduler
from gbot.memory.store import MemoryStore


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
    """Create owner user in DB at startup and ensure role='owner'."""
    if config.assistant.owner is None:
        return
    owner = config.assistant.owner
    db.get_or_create_user(owner.username, name=owner.name)
    db.set_user_role(owner.username, "owner")
    logger.info(f"Owner user ensured: {owner.username} (role=owner)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init Config → MemoryStore → GraphRunner → Background Services. Shutdown: cleanup."""
    from gbot.agent.delegation import DelegationPlanner
    from gbot.agent.tools import make_tools
    from gbot.agent.tools.delegate import make_delegate_tools
    from gbot.agent.tools.registry import build_background_registry, get_tool_catalog

    config = load_config()
    db = MemoryStore(str(config.db_path))
    _ensure_owner(config, db)

    # Create runner with empty tools first (chicken-and-egg: scheduler needs runner)
    runner = GraphRunner(config, db, tools=[])

    # Background services (need runner)
    cron_scheduler = CronScheduler(db, runner, config=config)
    heartbeat = HeartbeatService(config, runner)
    worker = SubagentWorker(config, db=db)

    # Build registry without delegation (planner doesn't exist yet)
    registry = make_tools(config, db)

    # Delegation planner uses background-safe subset of registry
    bg_registry = build_background_registry(registry)
    tool_catalog = get_tool_catalog(bg_registry)
    planner = DelegationPlanner(config, tool_catalog)

    # Now add delegation tools (worker + scheduler + planner ready)
    delegate_tools = make_delegate_tools(
        worker, cron_scheduler, planner, db=db,
    )
    if delegate_tools:
        registry.register_group(
            "delegation", delegate_tools, requires=["worker", "scheduler"],
        )

    # Rebuild runner with full registry
    from gbot.agent.graph import create_graph

    runner.registry = registry
    runner.tools = registry.get_all_tools()
    runner._graph = create_graph(config, db, runner.tools)

    # Context service for inspection and management
    from gbot.agent.context import ContextService

    context_service = ContextService(config, db, registry=registry)

    # Startup validation: check roles.yaml groups against registry
    from gbot.agent.permissions import _load_roles_yaml

    roles_data = _load_roles_yaml()
    if roles_data:
        for w in registry.validate_roles(roles_data):
            logger.warning(f"RBAC config: {w}")
    logger.info(
        f"ToolRegistry: {len(registry)} tools in "
        f"{len(registry.get_groups_summary())} groups"
    )

    await cron_scheduler.start()
    heartbeat_task = asyncio.create_task(heartbeat.start())

    # WebSocket connection registry for event push
    from gbot.api.ws import ConnectionManager

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
    app.state.context_service = context_service

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

    # Middleware - CORS restricted to known origins
    allowed_origins = [
        "https://gbot-assistant.cloud",
        "https://www.gbot-assistant.cloud",
        "http://localhost:3001",       # Dashboard dev/docker
        "http://127.0.0.1:3001",      # Dashboard docker
        "http://localhost:3000",       # Dashboard vite dev
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
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
