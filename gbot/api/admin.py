"""Admin API endpoints — server status, config, users, tasks, skills, logs, context."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from gbot import __version__
from gbot.agent.context import ContextService
from gbot.api.deps import get_config, get_context_service, get_current_user, get_db
from gbot.core.config.schema import Config
from gbot.memory.store import MemoryStore

_VALID_ROLES = {"owner", "member", "guest"}


class RoleUpdate(BaseModel):
    """Request body for role update."""

    role: str


class UserCreate(BaseModel):
    """Body for ``POST /admin/users``."""

    user_id: str
    name: str | None = None
    role: str = "member"
    password: str | None = None


class UserUpdate(BaseModel):
    """Body for ``PATCH /admin/users/{user_id}``. Every field optional —
    only provided ones are updated.
    """

    name: str | None = None
    role: str | None = None
    password: str | None = None


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

    from gbot.agent.skills.loader import SkillLoader

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
    result = []
    for u in users:
        d = dict(u)
        if "role" not in d:
            user_data = db.get_user(d["user_id"])
            d["role"] = (user_data or {}).get("role", "guest")
        result.append(d)
    return result


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


@router.post("/users")
async def create_user(
    body: UserCreate,
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    db: MemoryStore = Depends(get_db),
):
    """Create a new user (owner only)."""
    _require_owner(current_user, config)
    if not body.user_id or not body.user_id.strip():
        raise HTTPException(status_code=400, detail="user_id required")
    if body.role not in _VALID_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role '{body.role}'. Must be one of: {_VALID_ROLES}",
        )
    if db.user_exists(body.user_id):
        raise HTTPException(
            status_code=409, detail=f"User '{body.user_id}' already exists"
        )
    db.get_or_create_user(body.user_id, name=body.name)
    db.set_user_role(body.user_id, body.role)
    if body.password:
        db.set_user_password(body.user_id, body.password)
    return {
        "user_id": body.user_id, "name": body.name, "role": body.role,
    }


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    body: UserUpdate,
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    db: MemoryStore = Depends(get_db),
):
    """Update name / role / password — only provided fields touched."""
    _require_owner(current_user, config)
    if not db.user_exists(user_id):
        raise HTTPException(status_code=404, detail=f"User '{user_id}' not found")
    if body.role is not None and body.role not in _VALID_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role '{body.role}'. Must be one of: {_VALID_ROLES}",
        )
    if body.name is not None:
        db.update_user_name(user_id, body.name)
    if body.role is not None:
        db.set_user_role(user_id, body.role)
    if body.password is not None:
        db.set_user_password(user_id, body.password)
    user = db.get_user(user_id) or {}
    return {
        "user_id": user_id,
        "name": user.get("name"),
        "role": user.get("role"),
    }


@router.delete("/users/{user_id}")
async def delete_user_endpoint(
    user_id: str,
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    db: MemoryStore = Depends(get_db),
):
    """Delete a user and ALL their per-user rows (cascade).

    Refuses to delete the configured owner — that one's removed via
    config + DB edit, not the API.
    """
    _require_owner(current_user, config)
    owner_id = getattr(getattr(config.assistant, "owner", None), "username", None)
    if owner_id and user_id == owner_id:
        raise HTTPException(
            status_code=400,
            detail="Refusing to delete the configured owner via API. "
                   "Change config.assistant.owner first.",
        )
    if user_id == current_user:
        raise HTTPException(
            status_code=400, detail="Cannot delete the user you're authenticated as.",
        )
    existed = db.delete_user(user_id)
    if not existed:
        raise HTTPException(status_code=404, detail=f"User '{user_id}' not found")
    return {"ok": True, "user_id": user_id}


@router.get("/tasks")
async def admin_tasks(
    execution_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    db: MemoryStore = Depends(get_db),
):
    """List background tasks with optional filters."""
    _require_owner(current_user, config)
    return db.get_tasks(execution_type=execution_type, status=status)


@router.delete("/tasks/{task_id}")
async def admin_remove_task(
    task_id: str,
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    db: MemoryStore = Depends(get_db),
):
    """Cancel/remove a task."""
    _require_owner(current_user, config)
    db.cancel_task(task_id)
    return {"status": "cancelled", "task_id": task_id}


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

    from gbot.agent.context import ContextBuilder

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
            "SELECT COUNT(*) FROM background_tasks WHERE execution_type IN ('recurring', 'monitor') AND enabled = 1"
        ).fetchone()[0]
        reminder_count = conn.execute(
            "SELECT COUNT(*) FROM background_tasks WHERE execution_type = 'delayed' AND status = 'pending'"
        ).fetchone()[0]
        note_count = conn.execute(
            "SELECT COUNT(*) FROM user_notes"
        ).fetchone()[0]
        memory_count = conn.execute(
            "SELECT COUNT(*) FROM agent_memory"
        ).fetchone()[0]
        fact_count = conn.execute(
            "SELECT COUNT(*) FROM memory_facts WHERE valid_until IS NULL"
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
            "memory_facts": fact_count,
            "recurring_tasks": cron_count,
            "pending_delayed": reminder_count,
        },
    }


@router.get("/logs")
async def admin_logs(
    limit: int = Query(default=50, ge=1, le=500),
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    db: MemoryStore = Depends(get_db),
):
    """Recent delegation/execution logs."""
    _require_owner(current_user, config)
    return db.get_executions(limit=limit)


# ── Memory ─────────────────────────────────────────────


@router.get("/memory/{user_id}")
async def admin_memory(
    user_id: str,
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    db: MemoryStore = Depends(get_db),
):
    """Memory overview for a user: notes, facts, processing log."""
    _require_owner(current_user, config)
    return {
        "user_id": user_id,
        "notes": db.get_notes(user_id, limit=50),
        "preferences": db.get_preferences(user_id),
        "favorites": db.get_favorites(user_id),
        "facts": db.get_facts(user_id, valid_only=False, limit=100),
        "fact_stats": db.get_fact_stats(user_id),
        "processing_log": db.get_processing_log(user_id, limit=10),
    }


@router.get("/memory/{user_id}/relations")
async def admin_memory_relations(
    user_id: str,
    entity: str | None = Query(default=None, description="Filter by canonical entity"),
    limit: int = Query(default=200, ge=1, le=1000),
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    db: MemoryStore = Depends(get_db),
):
    """Faz 22D — list valid memory_relations for a user, optionally filtered
    by canonical entity name.
    """
    _require_owner(current_user, config)
    rels = db.get_relations(user_id, canonical=entity, limit=limit)
    return {"user_id": user_id, "entity": entity, "count": len(rels), "relations": rels}


@router.get("/memory/{user_id}/entities")
async def admin_memory_entities(
    user_id: str,
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    db: MemoryStore = Depends(get_db),
):
    """Faz 22D — distinct canonical entities for a user with relation
    counts. Useful for picking which entity to view a page for.
    """
    _require_owner(current_user, config)
    with db._get_conn() as conn:
        rows = conn.execute(
            """SELECT ent, SUM(c) AS total FROM (
                   SELECT canonical_source AS ent, COUNT(*) AS c
                       FROM memory_relations
                       WHERE user_id = ? AND canonical_source IS NOT NULL
                         AND canonical_source != '' AND valid_until IS NULL
                       GROUP BY canonical_source
                   UNION ALL
                   SELECT canonical_target AS ent, COUNT(*) AS c
                       FROM memory_relations
                       WHERE user_id = ? AND canonical_target IS NOT NULL
                         AND canonical_target != '' AND valid_until IS NULL
                       GROUP BY canonical_target
               )
               GROUP BY ent ORDER BY total DESC""",
            (user_id, user_id),
        ).fetchall()
    return {
        "user_id": user_id,
        "count": len(rows),
        "entities": [{"canonical": r["ent"], "relation_count": r["total"]} for r in rows],
    }


@router.get("/memory/{user_id}/entity-pages")
async def admin_memory_entity_pages(
    user_id: str,
    only_fresh: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    db: MemoryStore = Depends(get_db),
):
    """Faz 22D — list compiled entity pages for a user."""
    _require_owner(current_user, config)
    pages = db.list_entity_pages(user_id, only_fresh=only_fresh, limit=limit)
    return {"user_id": user_id, "count": len(pages), "pages": pages}


@router.post("/memory/{user_id}/pages/recompile")
async def admin_recompile_entity_page(
    user_id: str,
    entity: str = Query(..., description="Canonical entity name to recompile"),
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    db: MemoryStore = Depends(get_db),
):
    """Faz 22D — manually trigger recompile of an entity page. Bypasses
    the debounce window. Useful for testing the compiler or after a bulk
    edit.
    """
    _require_owner(current_user, config)
    from gbot.memory.entities import EntityResolver
    from gbot.memory.entity_pages import EntityPageCompiler

    owner = getattr(config.assistant, "owner", None)
    resolver = EntityResolver(
        db,
        owner_username=getattr(owner, "username", None) if owner else None,
        owner_display_name=getattr(owner, "name", None) if owner else None,
    )
    compiler = EntityPageCompiler(db, config.memory, resolver=resolver)
    if not compiler.enabled:
        return {
            "ok": False,
            "reason": "memory.entity_pages.enabled is false in config",
        }
    page = await compiler.compile_now(user_id, entity)
    return {
        "ok": page is not None,
        "user_id": user_id,
        "entity": entity,
        "page": page,
    }


@router.get("/memory/{user_id}/pages/{entity}/versions")
async def admin_list_page_versions(
    user_id: str,
    entity: str,
    limit: int = Query(default=20, ge=1, le=200),
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    db: MemoryStore = Depends(get_db),
):
    """Faz 22J — return the append-only compile-version history for an
    entity page. Useful for diffing recent revisions, debugging
    incremental updates, or rolling back via UI.
    """
    _require_owner(current_user, config)
    page = db.get_entity_page(user_id, entity)
    if not page:
        return {"ok": False, "reason": "page not found", "entity": entity}
    versions = db.list_page_versions(page["page_id"], limit=limit)
    return {
        "ok": True,
        "user_id": user_id,
        "entity": entity,
        "page_id": page["page_id"],
        "count": len(versions),
        "versions": versions,
    }


@router.post("/memory/{user_id}/pages/lint")
async def admin_lint_pages(
    user_id: str,
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    db: MemoryStore = Depends(get_db),
):
    """Faz 22J — sweep wiki pages for stale citations + orphan pages.
    Idempotent. Returns counts. Runs the same logic that the weekly
    maintenance pass invokes."""
    _require_owner(current_user, config)
    from gbot.memory.entities import EntityResolver
    from gbot.memory.entity_pages import EntityPageCompiler
    from gbot.memory.maintenance import MemoryMaintenance

    owner = getattr(config.assistant, "owner", None)
    resolver = EntityResolver(
        db,
        owner_username=getattr(owner, "username", None) if owner else None,
        owner_display_name=getattr(owner, "name", None) if owner else None,
    )
    compiler = EntityPageCompiler(db, config.memory, resolver=resolver)
    maintenance = MemoryMaintenance(db, config.memory, compiler=compiler)
    stats = await maintenance.lint_pages(user_id)
    return stats


@router.delete("/memory/{user_id}/entity/{entity}")
async def admin_forget_entity(
    user_id: str,
    entity: str,
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    db: MemoryStore = Depends(get_db),
):
    """Faz 22D Step 13 — cascade-archive an entity (all facts mentioning
    it, all relations involving it, and its compiled page).

    Audit-safe: facts/relations are invalidated (``valid_until`` set),
    not deleted. The entity page is hard-deleted (it's a derived view).
    """
    _require_owner(current_user, config)
    result = db.forget_entity(user_id, entity)
    return {"user_id": user_id, "entity": entity, "archived": result}


@router.post("/memory/{user_id}/maintenance/run")
async def admin_run_maintenance(
    user_id: str,
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    db: MemoryStore = Depends(get_db),
):
    """Faz 22D Step 12 — manually trigger memory maintenance for a user
    (decay + stale-page recompile + orphan cleanup + relations dedup).
    """
    _require_owner(current_user, config)
    from gbot.memory.entities import EntityResolver
    from gbot.memory.entity_pages import EntityPageCompiler
    from gbot.memory.maintenance import MemoryMaintenance

    owner = getattr(config.assistant, "owner", None)
    resolver = EntityResolver(
        db,
        owner_username=getattr(owner, "username", None) if owner else None,
        owner_display_name=getattr(owner, "name", None) if owner else None,
    )
    compiler = EntityPageCompiler(db, config.memory, resolver=resolver)
    maintenance = MemoryMaintenance(db, config.memory, compiler=compiler)
    return await maintenance.run_now(user_id)


# ── Faz 22G: lifecycle state controls ──────────────────────────


@router.get("/memory/{user_id}/facts")
async def admin_facts_by_state(
    user_id: str,
    state: str | None = Query(
        default=None,
        description="Filter by lifecycle state: active|weak|inhibited|archived",
    ),
    fact_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    db: MemoryStore = Depends(get_db),
):
    """List facts, optionally filtered by lifecycle state.

    With ``state=archived`` the legacy ``valid_only`` gate is dropped
    so audit-only rows show up.
    """
    _require_owner(current_user, config)
    valid_only = state != "archived"
    return {
        "user_id": user_id,
        "state": state,
        "facts": db.get_facts(
            user_id,
            fact_type=fact_type,
            valid_only=valid_only,
            state=state,
            limit=limit,
        ),
    }


@router.post("/memory/{user_id}/facts/{fact_id}/inhibit")
async def admin_inhibit_fact(
    user_id: str,
    fact_id: str,
    hold_days: int = Query(default=7, ge=1, le=90),
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    db: MemoryStore = Depends(get_db),
):
    """Faz 22G — temporarily hide a fact from retrieval.

    Defaults to a 7-day hold. ``apply_decay`` auto-restores the row to
    ``state='active'`` once ``inhibited_until`` lapses; restore early
    via the ``/restore`` endpoint.
    """
    _require_owner(current_user, config)
    ok = db.inhibit_fact(fact_id, hold_days=hold_days)
    return {"ok": ok, "fact_id": fact_id, "hold_days": hold_days}


@router.post("/memory/{user_id}/obsidian-sync/run")
async def admin_run_obsidian_sync(
    user_id: str,
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    db: MemoryStore = Depends(get_db),
):
    """Faz 22G Aşama 4 — manually trigger an Obsidian vault sync for
    the user. No-op + reports `enabled: false` when the feature is
    disabled in config.
    """
    _require_owner(current_user, config)
    from gbot.memory.obsidian_sync import ObsidianSyncer

    syncer = ObsidianSyncer(db, config.memory)
    return syncer.run(user_id)


@router.post("/memory/{user_id}/facts/{fact_id}/restore")
async def admin_restore_fact(
    user_id: str,
    fact_id: str,
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    db: MemoryStore = Depends(get_db),
):
    """Faz 22G — undo an INHIBITED fact, putting it back in ACTIVE."""
    _require_owner(current_user, config)
    ok = db.restore_fact(fact_id)
    return {"ok": ok, "fact_id": fact_id}


@router.get("/memory/{user_id}/retrieval-debug")
async def admin_retrieval_debug(
    user_id: str,
    query: str = Query(..., description="Query to embed and search against"),
    top_k: int = Query(default=10, ge=1, le=50),
    current_user: str = Depends(get_current_user),
    config: Config = Depends(get_config),
    db: MemoryStore = Depends(get_db),
):
    """Faz 22D — debug helper: embed a query and return raw distance
    scores for every candidate fact. Useful for tuning the distance gate.
    """
    _require_owner(current_user, config)
    from gbot.memory.embedder import MemoryEmbedder

    embedder = MemoryEmbedder(config.memory.embedding)
    query_emb = embedder.embed(query)
    candidates = db.search_similar_facts(
        user_id, query_emb, top_k=top_k, max_distance=None
    )
    gate = config.memory.retrieval.max_distance
    for f in candidates:
        f["above_gate"] = gate is not None and (f.get("distance") or 0) > gate
    return {
        "user_id": user_id,
        "query": query,
        "max_distance_gate": gate,
        "count": len(candidates),
        "candidates": candidates,
    }


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
        layers = ctx_service.get_planner_context(user_id=uid)
    elif profile == "light":
        layers = ctx_service.get_light_context(user_id=uid)
    else:
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
        layers = ctx_service.get_planner_context(user_id=uid)
        parts = [lr.content for lr in layers.values() if lr.enabled and lr.content]
        content = "\n\n---\n\n".join(parts)
    elif profile == "light":
        layers = ctx_service.get_light_context(user_id=uid)
        parts = [lr.content for lr in layers.values() if lr.enabled and lr.content]
        content = "\n\n---\n\n".join(parts)
    else:
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
