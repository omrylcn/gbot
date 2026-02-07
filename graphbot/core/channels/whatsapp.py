"""WhatsApp channel — stub (requires Node.js Baileys bridge)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["whatsapp"])


@router.post("/webhooks/whatsapp")
async def whatsapp_webhook():
    """Stub — WhatsApp requires external Node.js bridge (Baileys).

    Implementation needs:
    - Node.js service running @whiskeysockets/baileys
    - WebSocket bridge between Python and Node.js
    - QR code authentication flow
    - Message type handling (text, voice, media)
    - Auto-reconnect with backoff
    - Sender format: phone@s.whatsapp.net

    See: reference files/ascibot/nanobot/nanobot/channels/whatsapp.py
    """
    return JSONResponse(
        {"status": "not_implemented", "message": "WhatsApp channel requires Node.js Baileys bridge. See development plan."},
        status_code=501,
    )
