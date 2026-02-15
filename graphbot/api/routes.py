"""Core API routes — chat, sessions, health."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
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
    activities = db.get_recent_activities(user_id)
    return UserContextResponse(
        context_text=ctx_text,
        preferences=prefs,
        favorites=[ItemCard(id=f.get("item_id", ""), title=f.get("item_title", "")) for f in favs],
        recent_activities=activities,
    )
