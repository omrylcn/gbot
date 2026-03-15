"""Discord channel — stub (requires Gateway WebSocket client)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["discord"])


@router.post("/webhooks/discord")
async def discord_webhook():
    """Stub — Discord requires Gateway WebSocket for regular messages.

    Implementation needs:
    - WebSocket connection to Discord Gateway (wss://gateway.discord.gg)
    - Heartbeat protocol (opcode 1/10/11)
    - IDENTIFY payload (opcode 2) with bot token + intents
    - MESSAGE_CREATE event handler (opcode 0, t="MESSAGE_CREATE")
    - Rate limit handling (429 + retry-after)
    - Auto-reconnect on RECONNECT/INVALID_SESSION

    See: reference files/ascibot/nanobot/nanobot/channels/discord.py
    """
    return JSONResponse(
        {"status": "not_implemented", "message": "Discord channel requires Gateway WebSocket client. See development plan."},
        status_code=501,
    )
