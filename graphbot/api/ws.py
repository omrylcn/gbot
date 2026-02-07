"""WebSocket routes — basic chat (streaming in Faz 5+)."""

from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from graphbot.agent.runner import GraphRunner

router = APIRouter()


@router.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    """WebSocket chat endpoint.

    Client sends: {"user_id": "...", "message": "...", "session_id": "..."}
    Server responds: {"response": "...", "session_id": "..."}
    """
    await ws.accept()
    runner: GraphRunner = ws.app.state.runner

    try:
        while True:
            data = await ws.receive_json()
            user_id = data.get("user_id", "default")
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
