"""Feishu/Lark channel — stub (requires lark-oapi SDK)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["feishu"])


@router.post("/webhooks/feishu")
async def feishu_webhook():
    """Stub — Feishu requires lark-oapi SDK with WebSocket long connection.

    Implementation needs:
    - lark-oapi SDK (pip install lark-oapi)
    - WebSocket client running in daemon thread
    - Thread-safe message passing via asyncio.run_coroutine_threadsafe()
    - Deduplication cache (OrderedDict, limit 1000)
    - Event: im.message.receive_v1
    - Auth: app_id + app_secret (OAuth)
    - Message format: JSON-wrapped plain text

    See: reference files/ascibot/nanobot/nanobot/channels/feishu.py
    """
    return JSONResponse(
        {"status": "not_implemented", "message": "Feishu channel requires lark-oapi SDK. See development plan."},
        status_code=501,
    )
