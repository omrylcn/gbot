"""Core API routes — chat, sessions, health."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from loguru import logger

from graphbot import __version__
from graphbot.agent.runner import GraphRunner
from graphbot.api.deps import get_config, get_current_user, get_db, get_runner
from graphbot.core.config.schema import Config
from graphbot.memory.models import (
    ChatRequest,
    ChatResponse,
    HealthResponse,
    ItemCard,
    SessionInfo,
    UserContextResponse,
)
from graphbot.memory.store import MemoryStore

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    current_user: str = Depends(get_current_user),
    runner: GraphRunner = Depends(get_runner),
    config: Config = Depends(get_config),
):
    """Send a message and get assistant response."""
    # Auth enabled → use authenticated user_id
    # Auth disabled → get_current_user returns default, but body.user_id can override
    if config.auth_enabled:
        user_id = current_user
    else:
        user_id = body.user_id or current_user
    try:
        response, session_id = await runner.process(
            user_id=user_id,
            channel="api",
            message=body.message,
            session_id=body.session_id,
        )
        return ChatResponse(response=response, session_id=session_id)
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health", response_model=HealthResponse)
async def health():
    """Health check."""
    return HealthResponse(status="ok", agent_ready=True, version=__version__)


@router.get("/sessions/{user_id}", response_model=list[SessionInfo])
async def list_sessions(
    user_id: str,
    limit: int = Query(default=10, ge=1, le=100),
    current_user: str = Depends(get_current_user),
    db: MemoryStore = Depends(get_db),
    config: Config = Depends(get_config),
):
    """List user's sessions."""
    # Auth enabled → only own sessions
    if config.auth_enabled and user_id != current_user:
        raise HTTPException(status_code=403, detail="Access denied")
    rows = db.get_user_sessions(user_id, limit=limit)
    return [SessionInfo(**r) for r in rows]


@router.get("/session/{session_id}/history")
async def session_history(
    session_id: str,
    current_user: str = Depends(get_current_user),
    db: MemoryStore = Depends(get_db),
    config: Config = Depends(get_config),
):
    """Get all messages in a session."""
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if config.auth_enabled and session.get("user_id") != current_user:
        raise HTTPException(status_code=403, detail="Access denied")
    messages = db.get_session_messages(session_id)
    return {"session_id": session_id, "messages": messages}


@router.get("/session/{session_id}/stats")
async def session_stats(
    session_id: str,
    request: Request,
    current_user: str = Depends(get_current_user),
    db: MemoryStore = Depends(get_db),
    config: Config = Depends(get_config),
):
    """Session-level stats: messages, tokens, context breakdown, tools."""
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if config.auth_enabled and session.get("user_id") != current_user:
        raise HTTPException(status_code=403, detail="Access denied")

    user_id = session["user_id"]

    # Message counts
    messages = db.get_session_messages(session_id)
    user_msgs = sum(1 for m in messages if m["role"] == "user")
    assistant_msgs = sum(1 for m in messages if m["role"] == "assistant")
    tool_msgs = sum(1 for m in messages if m["role"] == "tool")

    # Context stats
    from graphbot.agent.context import ContextBuilder
    ctx = ContextBuilder(config, db)
    context_stats = ctx.get_context_stats(user_id)

    # Tool stats
    registry = request.app.state.runner.registry
    tool_total = len(registry.get_all_tools())

    token_count = session.get("token_count", 0)
    token_limit = config.assistant.session_token_limit
    token_pct = round(token_count / token_limit * 100, 1) if token_limit else 0

    return {
        "session_id": session_id,
        "user_id": user_id,
        "channel": session.get("channel", "api"),
        "started_at": session.get("started_at"),
        "active": session.get("ended_at") is None,
        "messages": {
            "total": len(messages),
            "user": user_msgs,
            "assistant": assistant_msgs,
            "tool_calls": tool_msgs,
        },
        "tokens": {
            "used": token_count,
            "limit": token_limit,
            "percent": token_pct,
        },
        "context": context_stats,
        "tools": tool_total,
        "model": config.assistant.model,
    }


@router.post("/session/{session_id}/end")
async def end_session(
    session_id: str,
    summary: str | None = None,
    current_user: str = Depends(get_current_user),
    db: MemoryStore = Depends(get_db),
    config: Config = Depends(get_config),
):
    """Manually close a session."""
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if config.auth_enabled and session.get("user_id") != current_user:
        raise HTTPException(status_code=403, detail="Access denied")
    if session.get("ended_at"):
        raise HTTPException(status_code=400, detail="Session already closed")
    db.end_session(session_id, summary=summary, close_reason="manual")
    return {"status": "closed", "session_id": session_id}


@router.get("/user/{user_id}/context", response_model=UserContextResponse)
async def user_context(
    user_id: str,
    current_user: str = Depends(get_current_user),
    db: MemoryStore = Depends(get_db),
    config: Config = Depends(get_config),
):
    """Get assembled user context."""
    if config.auth_enabled and user_id != current_user:
        raise HTTPException(status_code=403, detail="Access denied")
    ctx_text = db.get_user_context(user_id)
    prefs = db.get_preferences(user_id)
    favs = db.get_favorites(user_id)
    return UserContextResponse(
        context_text=ctx_text,
        preferences=prefs,
        favorites=[ItemCard(id=f.get("item_id", ""), title=f.get("item_title", "")) for f in favs],
    )


@router.get("/events/{user_id}")
async def get_events(
    user_id: str,
    current_user: str = Depends(get_current_user),
    db: MemoryStore = Depends(get_db),
    config: Config = Depends(get_config),
):
    """Get undelivered system events and mark them as delivered."""
    if config.auth_enabled and user_id != current_user:
        raise HTTPException(status_code=403, detail="Access denied")
    events = db.get_undelivered_events(user_id)
    if events:
        db.mark_events_delivered([e["id"] for e in events])
    return {"events": [dict(e) for e in events]}
