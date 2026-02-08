"""WebSocket routes — basic chat (streaming in Faz 14+)."""

from __future__ import annotations

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from loguru import logger

from graphbot.agent.runner import GraphRunner
from graphbot.core.config.schema import Config

router = APIRouter()


@router.websocket("/ws/chat")
async def ws_chat(ws: WebSocket, token: str | None = Query(None)):
    """WebSocket chat endpoint.

    Client sends: {"user_id": "...", "message": "...", "session_id": "..."}
    Server responds: {"response": "...", "session_id": "..."}

    When auth is enabled, pass token as query param: /ws/chat?token=<jwt>
    """
    config: Config = ws.app.state.config
    runner: GraphRunner = ws.app.state.runner

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

    try:
        while True:
            data = await ws.receive_json()
            user_id = default_user if config.auth_enabled else data.get("user_id", default_user)
            message = data.get("message", "")
            session_id = data.get("session_id")

            if not message:
                await ws.send_json({"error": "Empty message"})
                continue

            try:
                response, sid = await runner.process(
                    user_id=user_id,
                    channel="ws",
                    message=message,
                    session_id=session_id,
                )
                await ws.send_json({"response": response, "session_id": sid})
            except Exception as e:
                logger.error(f"WS chat error: {e}")
                await ws.send_json({"error": str(e)})
    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected")
