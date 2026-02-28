"""WhatsApp channel — webhook handler + send helper via WAHA."""

from __future__ import annotations

import re

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from loguru import logger

from graphbot.agent.runner import GraphRunner
from graphbot.agent.tools.messaging import BOT_PREFIX
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

    # "message" = incoming only; "message.any" = all (incoming + outgoing).
    # Skip "message.any" for incoming (fromMe=False) to avoid duplicate processing.
    event_type = body.get("event", "")
    if event_type == "message.any":
        if not body.get("payload", {}).get("fromMe", False):
            return JSONResponse({"ok": True})
    elif event_type != "message":
        return JSONResponse({"ok": True})

    message = body.get("payload", {})
    is_from_me = message.get("fromMe", False)

    # Extract text content
    text = (message.get("body") or "").strip()
    logger.debug(
        f"WhatsApp raw: event={event_type}, fromMe={is_from_me}, "
        f"from={message.get('from','')}, text={text[:80]!r}"
    )
    if not text:
        return JSONResponse({"ok": True})

    chat_id = message.get("from", "")  # "905551234567@c.us", "XXX@g.us", or "YYY@lid"
    # Ignore non-chat sources (newsletters, broadcasts, etc.)
    valid_suffixes = ("@c.us", "@g.us", "@lid")
    if not any(chat_id.endswith(s) for s in valid_suffixes):
        return JSONResponse({"ok": True})
    is_group = chat_id.endswith("@g.us")
    config = request.app.state.config
    allowed_groups = config.channels.whatsapp.allowed_groups
    if not is_group:
        wa_config = config.channels.whatsapp

        if not wa_config.monitor_dm and not wa_config.respond_to_dm:
            # DM processing completely disabled
            return JSONResponse({"ok": True})

        # Check allowed_dms whitelist — keys are phone numbers or LIDs
        sender_id = WAHAClient.chat_id_to_phone(chat_id)
        if wa_config.allowed_dms and sender_id not in wa_config.allowed_dms:
            return JSONResponse({"ok": True})

        if is_from_me:
            return JSONResponse({"ok": True})

        # Resolve sender name: config dict value > DB user > raw ID
        sender_name = wa_config.allowed_dms.get(sender_id, sender_id)
        sender_user_id = db.resolve_user("whatsapp", sender_id)
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
            # Process DM through runner — LLM decides whether to reply.
            # Response stays in owner's session; LLM uses send_message_to_user
            # tool if it wants to message the DM sender directly.
            logger.debug(f"WhatsApp DM (respond): {sender_name} → {user_id}: {text[:50]}")
            dm_message = (
                f"[WhatsApp DM from {sender_name}]: {text}\n"
                f"[If you want to reply to {sender_name}, use send_message_to_user tool.]"
            )
            try:
                await runner.process(
                    user_id=user_id,
                    channel="whatsapp",
                    message=dm_message,
                    session_id=sid,
                )
            except Exception as e:
                logger.error(f"WhatsApp DM processing error: {e}")
        else:
            # monitor_dm=true → store DM in session but do NOT respond.
            db.add_message(sid, "user", f"[WhatsApp DM] {sender_name}: {text}")
            logger.debug(f"WhatsApp DM stored: {sender_name} → {user_id}: {text[:50]}")

        return JSONResponse({"ok": True})

    # Group — must be in allowed list
    if allowed_groups and chat_id not in allowed_groups:
        return JSONResponse({"ok": True})

    # Skip bot's own responses (they start with BOT_PREFIX) to prevent loops
    if is_from_me and text.startswith(BOT_PREFIX.strip()):
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

    await send_whatsapp_message(config.channels.whatsapp, chat_id, response)

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
    # Try both phone and LID since participant may use either format
    user_id = db.resolve_user("whatsapp", sender_phone)
    if not user_id:
        logger.warning(f"Unknown WhatsApp sender: {sender_phone}")
        return JSONResponse({"ok": True})

    # Delegate to user-specific handler
    return await whatsapp_webhook(user_id, request, db, runner)


async def send_whatsapp_message(
    wa_config: WhatsAppChannelConfig, chat_id: str, text: str
) -> None:
    """Send a message via WAHA API, splitting long messages if necessary.

    Auto-prefixes ``[gbot]`` if not already present — on WhatsApp, every
    outgoing message comes from the bot's phone number, so the recipient
    must always be able to distinguish bot messages from owner messages.
    """
    if not text:
        return

    # Strip ALL [gbot] variants from anywhere in text (plain, bold, repeated).
    # LLM sometimes adds **[gbot]** or [gbot] in its response — remove them all,
    # then prepend exactly one clean prefix.
    text = re.sub(r'\*{0,2}\[gbot\]\*{0,2}\s*', '', text, flags=re.IGNORECASE).strip()
    text = f"{BOT_PREFIX}{text}"

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
