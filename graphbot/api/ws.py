"""WebSocket routes — unified chat + event delivery."""

from __future__ import annotations

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from loguru import logger

from graphbot.agent.runner import GraphRunner
from graphbot.core.config.schema import Config
from graphbot.memory.store import MemoryStore

router = APIRouter()


# ── Connection Registry ──────────────────────────────────────


class ConnectionManager:
    """In-memory WebSocket connection registry.

    Tracks active WebSocket connections per user_id.
    Thread-safe via asyncio (single event loop).
    """

    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = {}

    def connect(self, user_id: str, ws: WebSocket) -> None:
        """Register a WebSocket connection for a user."""
        if user_id not in self._connections:
            self._connections[user_id] = set()
        self._connections[user_id].add(ws)
        logger.debug(
            f"WS connected: user={user_id}, "
            f"total={len(self._connections[user_id])}"
        )

    def disconnect(self, user_id: str, ws: WebSocket) -> None:
        """Unregister a WebSocket connection."""
        if user_id in self._connections:
            self._connections[user_id].discard(ws)
            if not self._connections[user_id]:
                del self._connections[user_id]
        logger.debug(f"WS disconnected: user={user_id}")

    def is_connected(self, user_id: str) -> bool:
        """Check if user has any active WebSocket connections."""
        return bool(self._connections.get(user_id))

    async def send_event(self, user_id: str, event: dict) -> bool:
        """Push an event to all active connections for a user.

        Returns True if at least one connection received the event.
        Silently removes broken connections.
        """
        conns = self._connections.get(user_id)
        if not conns:
            return False

        sent = False
        broken: list[WebSocket] = []
        for ws in conns:
            try:
                await ws.send_json(event)
                sent = True
            except Exception:
                broken.append(ws)

        for ws in broken:
            conns.discard(ws)
        if not conns:
            del self._connections[user_id]

        return sent


# ── WebSocket Endpoint ───────────────────────────────────────


@router.websocket("/ws/chat")
async def ws_chat(ws: WebSocket, token: str | None = Query(None)):
    """WebSocket chat endpoint — unified chat + event delivery.

    Client sends: {"message": "...", "user_id": "...", "session_id": "..."}
    Server chat:  {"type": "chat", "response": "...", "session_id": "..."}
    Server event: {"type": "event", "event_type": "...", "source": "...", "payload": "..."}

    When auth is enabled, pass token as query param: /ws/chat?token=<jwt>
    """
    config: Config = ws.app.state.config
    runner: GraphRunner = ws.app.state.runner
    db: MemoryStore = ws.app.state.db
    manager: ConnectionManager = ws.app.state.ws_manager

    # Resolve user_id from token or default
    default_user = config.owner_user_id or "default"
    if config.auth_enabled and token:
        from graphbot.api.auth import decode_token

        try:
            default_user = decode_token(
                token, config.auth.jwt_secret_key, config.auth.jwt_algorithm
            )
        except Exception:
            await ws.close(code=4001, reason="Invalid token")
            return
    elif config.auth_enabled and not token:
        await ws.close(code=4001, reason="Authentication required")
        return

    await ws.accept()
    user_id = default_user
    manager.connect(user_id, ws)

    try:
        # Flush undelivered events on connect
        events = db.get_undelivered_events(user_id, limit=10)
        if events:
            for e in events:
                await ws.send_json({
                    "type": "event",
                    "event_type": e.get("event_type", ""),
                    "source": e.get("source", ""),
                    "payload": e.get("payload", ""),
                })
            db.mark_events_delivered([e["id"] for e in events])

        while True:
            data = await ws.receive_json()
            msg_user_id = (
                default_user
                if config.auth_enabled
                else data.get("user_id", default_user)
            )
            message = data.get("message", "")
            session_id = data.get("session_id")

            if not message:
                await ws.send_json({"type": "error", "error": "Empty message"})
                continue

            try:
                response, sid = await runner.process(
                    user_id=msg_user_id,
                    channel="ws",
                    message=message,
                    session_id=session_id,
                )
                await ws.send_json({
                    "type": "chat",
                    "response": response,
                    "session_id": sid,
                })
            except Exception as e:
                logger.error(f"WS chat error: {e}")
                await ws.send_json({"type": "error", "error": str(e)})
    except WebSocketDisconnect:
        manager.disconnect(user_id, ws)
        logger.debug(f"WebSocket client disconnected: {user_id}")
