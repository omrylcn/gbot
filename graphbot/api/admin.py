"""Admin API endpoints — server status, config, users, crons, skills, logs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from graphbot import __version__
from graphbot.api.deps import get_config, get_current_user, get_db
from graphbot.core.config.schema import Config
from graphbot.memory.store import MemoryStore

_VALID_ROLES = {"owner", "member", "guest"}


class RoleUpdate(BaseModel):
    """Request body for role update."""

    role: str

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


@router.get("/logs")
async def admin_logs(
    limit: int = Query(default=50, ge=1, le=500),
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    db: MemoryStore = Depends(get_db),
):
    """Recent activity logs."""
    _require_owner(current_user, config)

    with db._get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM activity_logs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
