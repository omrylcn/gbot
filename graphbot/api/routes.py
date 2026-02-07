"""Core API routes — chat, sessions, health."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger

from graphbot.agent.runner import GraphRunner
from graphbot.api.deps import get_config, get_db, get_runner
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
    runner: GraphRunner = Depends(get_runner),
    config: Config = Depends(get_config),
):
    """Send a message and get assistant response."""
    user_id = body.user_id or config.owner_user_id or "default"
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
    return HealthResponse(status="ok", agent_ready=True)


@router.get("/sessions/{user_id}", response_model=list[SessionInfo])
async def list_sessions(
    user_id: str,
    limit: int = Query(default=10, ge=1, le=100),
    db: MemoryStore = Depends(get_db),
):
    """List user's sessions."""
    rows = db.get_user_sessions(user_id, limit=limit)
    return [SessionInfo(**r) for r in rows]


@router.get("/session/{session_id}/history")
async def session_history(
    session_id: str,
    db: MemoryStore = Depends(get_db),
):
    """Get all messages in a session."""
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = db.get_session_messages(session_id)
    return {"session_id": session_id, "messages": messages}


@router.post("/session/{session_id}/end")
async def end_session(
    session_id: str,
    summary: str | None = None,
    db: MemoryStore = Depends(get_db),
):
    """Manually close a session."""
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.get("ended_at"):
        raise HTTPException(status_code=400, detail="Session already closed")
    db.end_session(session_id, summary=summary, close_reason="manual")
    return {"status": "closed", "session_id": session_id}


@router.get("/user/{user_id}/context", response_model=UserContextResponse)
async def user_context(
    user_id: str,
    db: MemoryStore = Depends(get_db),
):
    """Get assembled user context."""
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
