"""WhatsApp channel — webhook handler + send helper via WAHA."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from loguru import logger

from graphbot.agent.runner import GraphRunner
from graphbot.api.deps import get_db, get_runner
from graphbot.core.channels.waha_client import WAHAClient
from graphbot.core.config.schema import WhatsAppChannelConfig
from graphbot.memory.store import MemoryStore

router = APIRouter(tags=["whatsapp"])


@router.post("/webhooks/whatsapp/{user_id}")
async def whatsapp_webhook(
    user_id: str,
    request: Request,
    db: MemoryStore = Depends(get_db),
    runner: GraphRunner = Depends(get_runner),
):
    """Handle incoming WAHA webhook events for a specific user.

    Each user's WhatsApp phone number is stored in user_channels.
    The user_id in the path identifies which user this webhook belongs to.
    """
    # Verify user exists and has a whatsapp link
    link = db.get_channel_link(user_id, "whatsapp")
    if not link:
        logger.debug(f"WhatsApp webhook: unknown user_id={user_id}")
        return JSONResponse({"error": "Unknown user"}, status_code=404)

    body = await request.json()

    # Only process "message" events (skip "message.any" to avoid duplicates)
    event_type = body.get("event", "")
    if event_type == "message.any":
        # message.any includes fromMe — only use it for fromMe messages
        # (regular incoming messages already arrive via "message" event)
        if not body.get("payload", {}).get("fromMe", False):
            return JSONResponse({"ok": True})
    elif event_type != "message":
        return JSONResponse({"ok": True})

    message = body.get("payload", {})
    is_from_me = message.get("fromMe", False)

    # Extract text content
    text = message.get("body", "").strip()
    logger.debug(
        f"WhatsApp raw: event={event_type}, fromMe={is_from_me}, "
        f"from={message.get('from','')}, text={text[:80]!r}"
    )
    if not text:
        return JSONResponse({"ok": True})

    chat_id = message.get("from", "")  # "905551234567@c.us" or "XXX@g.us"
    # Ignore non-chat sources (newsletters, broadcasts, etc.)
    if not chat_id.endswith("@c.us") and not chat_id.endswith("@g.us"):
        return JSONResponse({"ok": True})
    is_group = chat_id.endswith("@g.us")
    config = request.app.state.config
    allowed_groups = config.channels.whatsapp.allowed_groups
    if not is_group:
        wa_config = config.channels.whatsapp

        if not wa_config.monitor_dm and not wa_config.respond_to_dm:
            # DM processing completely disabled
            return JSONResponse({"ok": True})

        # Save chat_id for proactive messaging
        db.update_channel_metadata_by_user(
            user_id, "whatsapp", {"chat_id": chat_id}
        )

        if is_from_me:
            return JSONResponse({"ok": True})

        # Resolve sender name
        sender_phone = WAHAClient.chat_id_to_phone(chat_id)
        sender_name = sender_phone
        sender_user_id = db.resolve_user("whatsapp", sender_phone)
        if sender_user_id:
            sender_obj = db.get_user(sender_user_id)
            if sender_obj and sender_obj.get("name"):
                sender_name = sender_obj["name"]

        # Get or create the owner's whatsapp session
        active = db.get_active_session(user_id, channel="whatsapp")
        if not active:
            sid = db.create_session(user_id, channel="whatsapp")
        else:
            sid = active["session_id"]

        if wa_config.respond_to_dm:
            # Bot phone mode — respond to DMs with [gbot] prefix
            logger.debug(f"WhatsApp DM (respond): {sender_name} → {user_id}: {text[:50]}")
            try:
                response, _session_id = await runner.process(
                    user_id=user_id,
                    channel="whatsapp",
                    message=text,
                    session_id=sid,
                )
            except Exception as e:
                logger.error(f"WhatsApp DM processing error: {e}")
                response = "An error occurred while processing your message."
            await send_whatsapp_message(
                wa_config, chat_id, f"[gbot] {response}"
            )
        else:
            # monitor_dm=true → store DM in session but do NOT respond.
            db.add_message(sid, "user", f"[WhatsApp DM] {sender_name}: {text}")
            logger.debug(f"WhatsApp DM stored: {sender_name} → {user_id}: {text[:50]}")

        return JSONResponse({"ok": True})

    # Group — must be in allowed list
    if allowed_groups and chat_id not in allowed_groups:
        return JSONResponse({"ok": True})

    # Skip bot's own responses (they start with [gbot]) to prevent loops
    if is_from_me and text.startswith("[gbot]"):
        return JSONResponse({"ok": True})

    # Session management (channel-isolated)
    active = db.get_active_session(user_id, channel="whatsapp")
    if not active:
        session_id = db.create_session(user_id, channel="whatsapp")
    else:
        session_id = active["session_id"]

    # Allowed group → respond to everything (like Telegram)
    logger.debug(f"WhatsApp group: user={user_id}, text={text[:50]}")
    try:
        response, _session_id = await runner.process(
            user_id=user_id,
            channel="whatsapp",
            message=text,
            session_id=session_id,
        )
    except Exception as e:
        logger.error(f"WhatsApp: processing error: {e}")
        response = "An error occurred while processing your message."

    await send_whatsapp_message(
        config.channels.whatsapp, chat_id, f"[gbot] {response}"
    )

    return JSONResponse({"ok": True})


@router.post("/webhooks/whatsapp")
async def whatsapp_webhook_global(
    request: Request,
    db: MemoryStore = Depends(get_db),
    runner: GraphRunner = Depends(get_runner),
):
    """Global webhook — auto-route by sender phone via user_channels table.

    Use this when WAHA sends all events to a single webhook URL.
    Only processes messages from allowed groups (DMs are ignored).
    """
    body = await request.json()

    # Same event filtering as user-specific handler
    event_type = body.get("event", "")
    if event_type == "message.any":
        if not body.get("payload", {}).get("fromMe", False):
            return JSONResponse({"ok": True})
    elif event_type != "message":
        return JSONResponse({"ok": True})

    message = body.get("payload", {})
    chat_id = message.get("from", "")

    # Only process allowed group messages — ignore DMs, newsletters, etc.
    if not chat_id.endswith("@g.us"):
        return JSONResponse({"ok": True})

    config = request.app.state.config
    allowed_groups = config.channels.whatsapp.allowed_groups
    if allowed_groups and chat_id not in allowed_groups:
        return JSONResponse({"ok": True})

    # For groups, the actual sender is in "participant"
    sender_id = message.get("participant", chat_id)
    sender_phone = WAHAClient.chat_id_to_phone(sender_id)
    if not sender_phone:
        return JSONResponse({"ok": True})

    # Resolve phone → user_id via user_channels table
    user_id = db.resolve_user("whatsapp", sender_phone)
    if not user_id:
        logger.warning(f"Unknown WhatsApp sender: {sender_phone}")
        return JSONResponse({"ok": True})

    # Delegate to user-specific handler
    return await whatsapp_webhook(user_id, request, db, runner)


async def send_whatsapp_message(
    wa_config: WhatsAppChannelConfig, chat_id: str, text: str
) -> None:
    """Send a message via WAHA API, splitting long messages if necessary."""
    if not text:
        return

    client = WAHAClient(wa_config.waha_url, wa_config.session, wa_config.api_key)
    chunks = split_message(text)

    for chunk in chunks:
        try:
            await client.send_text(chat_id, chunk)
        except httpx.HTTPStatusError as e:
            logger.error(f"WhatsApp send failed ({e.response.status_code}): {e}")
        except Exception as e:
            logger.error(f"WhatsApp send failed: {e}")


def split_message(text: str, max_length: int = 4096) -> list[str]:
    """Split long messages at paragraph boundaries.

    Parameters
    ----------
    text : str
        Message text.
    max_length : int
        Maximum length per chunk (WhatsApp limit: 4096).

    Returns
    -------
    list[str]
        Message chunks.
    """
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    current = ""

    for paragraph in text.split("\n\n"):
        if len(current) + len(paragraph) + 2 > max_length:
            if current:
                chunks.append(current.strip())
                current = ""
            # Single paragraph exceeds max — split by lines, then hard-cut
            if len(paragraph) > max_length:
                for line in paragraph.split("\n"):
                    # Hard-cut lines that still exceed max
                    while len(line) > max_length:
                        chunks.append(line[:max_length])
                        line = line[max_length:]
                    if len(current) + len(line) + 1 > max_length:
                        if current:
                            chunks.append(current.strip())
                        current = line
                    else:
                        current = f"{current}\n{line}" if current else line
            else:
                current = paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph

    if current.strip():
        chunks.append(current.strip())

    return chunks
