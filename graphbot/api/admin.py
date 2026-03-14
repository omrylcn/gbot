"""Admin API endpoints — server status, config, users, crons, skills, logs, context."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from graphbot import __version__
from graphbot.agent.context import ContextService
from graphbot.api.deps import get_config, get_context_service, get_current_user, get_db
from graphbot.core.config.schema import Config
from graphbot.memory.store import MemoryStore

_VALID_ROLES = {"owner", "member", "guest"}


class RoleUpdate(BaseModel):
    """Request body for role update."""

    role: str


class LayerOverrideRequest(BaseModel):
    """Request body for layer override."""

    content: str | None = None
    enabled: bool = True

router = APIRouter(prefix="/admin", tags=["admin"])


def _require_owner(current_user: str, config: Config) -> None:
    """Raise 403 if auth is enabled and user is not the owner."""
    if config.auth_enabled and current_user != config.owner_user_id:
        raise HTTPException(status_code=403, detail="Owner access required")


@router.get("/status")
async def admin_status(
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    db: MemoryStore = Depends(get_db),
):
    """Server status overview."""
    _require_owner(current_user, config)

    with db._get_conn() as conn:
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active_sessions = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE ended_at IS NULL"
        ).fetchone()[0]

    return {
        "version": __version__,
        "model": config.assistant.model,
        "users": user_count,
        "active_sessions": active_sessions,
        "status": "running",
    }


@router.get("/config")
async def admin_config(
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
):
    """Sanitized server configuration (API keys masked)."""
    _require_owner(current_user, config)

    return {
        "model": config.assistant.model,
        "temperature": config.assistant.temperature,
        "session_token_limit": config.assistant.session_token_limit,
        "max_iterations": config.assistant.max_iterations,
        "auth_enabled": config.auth_enabled,
        "cron_enabled": config.background.cron.enabled,
        "heartbeat_enabled": config.background.heartbeat.enabled,
        "db_path": config.database.path,
    }


@router.get("/skills")
async def admin_skills(
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
):
    """List discovered skills."""
    _require_owner(current_user, config)

    from pathlib import Path

    from graphbot.agent.skills.loader import SkillLoader

    builtin_dir = Path(__file__).parent.parent / "agent" / "skills" / "builtin"
    loader = SkillLoader(config.workspace_path, builtin_dir)
    skills = loader.discover()
    return [
        {"name": s.name, "description": s.description, "always": s.always}
        for s in skills
    ]


@router.get("/users")
async def admin_users(
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    db: MemoryStore = Depends(get_db),
):
    """List all users."""
    _require_owner(current_user, config)
    users = db.list_users()
    return [dict(u) for u in users]


@router.put("/users/{user_id}/role")
async def set_user_role(
    user_id: str,
    body: RoleUpdate,
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    db: MemoryStore = Depends(get_db),
):
    """Set user role (owner only)."""
    _require_owner(current_user, config)
    if body.role not in _VALID_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role '{body.role}'. Must be one of: {_VALID_ROLES}",
        )
    if not db.user_exists(user_id):
        raise HTTPException(status_code=404, detail=f"User '{user_id}' not found")
    db.set_user_role(user_id, body.role)
    return {"user_id": user_id, "role": body.role}


@router.get("/crons")
async def admin_crons(
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    db: MemoryStore = Depends(get_db),
):
    """List all cron jobs."""
    _require_owner(current_user, config)
    jobs = db.get_cron_jobs()
    return [dict(j) for j in jobs]


@router.delete("/crons/{job_id}")
async def admin_remove_cron(
    job_id: str,
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    db: MemoryStore = Depends(get_db),
):
    """Remove a cron job."""
    _require_owner(current_user, config)
    db.remove_cron_job(job_id)
    return {"status": "removed", "job_id": job_id}


@router.get("/tools")
async def admin_tools(
    request: Request,
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
):
    """List all registered tools with metadata, groups, and availability."""
    _require_owner(current_user, config)
    registry = request.app.state.runner.registry
    return {
        "tools": registry.get_catalog(),
        "groups": registry.get_groups_summary(),
        "total": len(registry),
        "available": len(registry.get_all_tools()),
    }


@router.get("/stats")
async def admin_stats(
    request: Request,
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    db: MemoryStore = Depends(get_db),
):
    """Comprehensive system stats: context, tools, sessions, tokens."""
    _require_owner(current_user, config)

    from graphbot.agent.context import ContextBuilder

    # Context stats for owner
    ctx = ContextBuilder(config, db)
    context_stats = ctx.get_context_stats(config.owner_user_id)

    # Tool stats
    registry = request.app.state.runner.registry
    tool_groups = registry.get_groups_summary()
    tool_total = len(registry)
    tool_available = len(registry.get_all_tools())

    # Session & token stats
    with db._get_conn() as conn:
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active_sessions = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE ended_at IS NULL"
        ).fetchone()[0]
        total_sessions = conn.execute(
            "SELECT COUNT(*) FROM sessions"
        ).fetchone()[0]
        total_tokens = conn.execute(
            "SELECT COALESCE(SUM(token_count), 0) FROM sessions"
        ).fetchone()[0]
        total_messages = conn.execute(
            "SELECT COUNT(*) FROM messages"
        ).fetchone()[0]
        cron_count = conn.execute(
            "SELECT COUNT(*) FROM cron_jobs WHERE enabled = 1"
        ).fetchone()[0]
        reminder_count = conn.execute(
            "SELECT COUNT(*) FROM reminders WHERE status = 'pending'"
        ).fetchone()[0]
        note_count = conn.execute(
            "SELECT COUNT(*) FROM user_notes"
        ).fetchone()[0]
        memory_count = conn.execute(
            "SELECT COUNT(*) FROM agent_memory"
        ).fetchone()[0]

    return {
        "system": {
            "version": __version__,
            "model": config.assistant.model,
            "session_token_limit": config.assistant.session_token_limit,
            "thinking": config.assistant.thinking,
        },
        "context": context_stats,
        "tools": {
            "total": tool_total,
            "available": tool_available,
            "groups": tool_groups,
        },
        "sessions": {
            "active": active_sessions,
            "total": total_sessions,
            "total_tokens": total_tokens,
        },
        "data": {
            "users": user_count,
            "messages": total_messages,
            "notes": note_count,
            "memories": memory_count,
            "cron_jobs": cron_count,
            "reminders": reminder_count,
        },
    }


@router.get("/logs")
async def admin_logs(
    limit: int = Query(default=50, ge=1, le=500),
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    db: MemoryStore = Depends(get_db),
):
    """Recent delegation logs."""
    _require_owner(current_user, config)

    with db._get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM delegation_log ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Context Inspection ─────────────────────────────────


@router.get("/context/{profile}/layers")
async def admin_context_layers(
    profile: str,
    user_id: str = Query(default=None),
    role: str = Query(default=None),
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    ctx_service: ContextService = Depends(get_context_service),
):
    """Layer-by-layer context breakdown for an agent profile."""
    _require_owner(current_user, config)
    uid = user_id or config.owner_user_id or "default"

    if profile == "planner":
        return ctx_service.get_planner_context()
    if profile == "light":
        return ctx_service.get_light_context()

    layers = ctx_service.get_layers(uid, profile=profile, role=role)
    return {
        "profile": profile,
        "user_id": uid,
        "layers": [lr.model_dump() for lr in layers.values()],
        "total_chars": sum(lr.chars for lr in layers.values()),
        "total_tokens": sum(lr.tokens for lr in layers.values()),
    }


@router.get("/context/{profile}/preview")
async def admin_context_preview(
    profile: str,
    user_id: str = Query(default=None),
    role: str = Query(default=None),
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    ctx_service: ContextService = Depends(get_context_service),
):
    """Full rendered context string for an agent profile."""
    _require_owner(current_user, config)
    uid = user_id or config.owner_user_id or "default"

    if profile == "planner":
        data = ctx_service.get_planner_context()
        return {"profile": "planner", "content": data["content"], "tokens": data["rendered_tokens"]}
    if profile == "light":
        data = ctx_service.get_light_context()
        return {"profile": "light", "content": data["content"], "tokens": data["full_tokens"]}

    content = ctx_service.preview(uid, profile=profile, role=role)
    return {
        "profile": profile,
        "user_id": uid,
        "content": content,
        "chars": len(content),
        "tokens": len(content) // 4,
    }


@router.put("/context/{profile}/layers/{layer}")
async def admin_override_layer(
    profile: str,
    layer: str,
    body: LayerOverrideRequest,
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    ctx_service: ContextService = Depends(get_context_service),
):
    """Override a layer's content at runtime (not persisted)."""
    _require_owner(current_user, config)
    ctx_service.set_override(profile, layer, content=body.content, enabled=body.enabled)
    return {"status": "ok", "profile": profile, "layer": layer, "override": body.model_dump()}


@router.delete("/context/{profile}/layers/{layer}")
async def admin_clear_layer_override(
    profile: str,
    layer: str,
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    ctx_service: ContextService = Depends(get_context_service),
):
    """Clear a runtime layer override."""
    _require_owner(current_user, config)
    ctx_service.clear_override(profile, layer)
    return {"status": "cleared", "profile": profile, "layer": layer}


@router.get("/context/overrides")
async def admin_list_overrides(
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    ctx_service: ContextService = Depends(get_context_service),
):
    """List all active runtime overrides."""
    _require_owner(current_user, config)
    result = {}
    for p in ("main", "planner", "light"):
        overrides = ctx_service.get_overrides(p)
        if overrides:
            result[p] = {
                name: ov.model_dump() for name, ov in overrides.items()
            }
    return result


@router.delete("/context/overrides")
async def admin_clear_all_overrides(
    profile: str = Query(default=None),
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    ctx_service: ContextService = Depends(get_context_service),
):
    """Clear all runtime overrides (optionally filtered by profile)."""
    _require_owner(current_user, config)
    ctx_service.clear_all_overrides(profile)
    return {"status": "cleared", "profile": profile or "all"}


# ── Profile Endpoints ──────────────────────────────────


@router.get("/profiles")
async def admin_profiles(
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    ctx_service: ContextService = Depends(get_context_service),
):
    """List all agent profiles."""
    _require_owner(current_user, config)
    return ctx_service.list_profiles()


@router.get("/profiles/{name}")
async def admin_profile_detail(
    name: str,
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    ctx_service: ContextService = Depends(get_context_service),
):
    """Profile detail with AGENT.md content."""
    _require_owner(current_user, config)
    return ctx_service.get_profile_detail(name)
