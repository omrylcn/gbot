"""Telegram channel — webhook handler + send helper."""

from __future__ import annotations

import re

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from loguru import logger

from graphbot.agent.runner import GraphRunner
from graphbot.api.deps import get_db, get_runner
from graphbot.memory.store import MemoryStore

router = APIRouter(tags=["telegram"])

TELEGRAM_API = "https://api.telegram.org/bot{token}"


@router.post("/webhooks/telegram/{user_id}")
async def telegram_webhook(
    user_id: str,
    request: Request,
    db: MemoryStore = Depends(get_db),
    runner: GraphRunner = Depends(get_runner),
):
    """Handle incoming Telegram webhook updates.

    Each user has their own bot token stored in user_channels.
    The user_id in the path identifies which user this webhook belongs to.
    """
    # Verify user exists and has a telegram link
    link = db.get_channel_link(user_id, "telegram")
    if not link:
        return JSONResponse({"error": "Unknown user"}, status_code=404)

    token = link["channel_user_id"]

    body = await request.json()

    # Extract message (skip non-message updates)
    message = body.get("message")
    if not message or not message.get("text"):
        return JSONResponse({"ok": True})

    chat_id = message["chat"]["id"]
    text = message["text"]

    # Save chat_id for proactive messaging
    db.update_channel_metadata_by_user(user_id, "telegram", {"chat_id": chat_id})
    active = db.get_active_session(user_id, channel="telegram")
    session_id = active["session_id"] if active else None

    # Process message
    try:
        response, _session_id = await runner.process(
            user_id=user_id, channel="telegram", message=text, session_id=session_id
        )
    except Exception as e:
        logger.error(f"Telegram: processing error: {e}")
        response = "An error occurred while processing your message."

    # Send response
    await send_message(token, chat_id, response)

    return JSONResponse({"ok": True})


async def send_message(token: str, chat_id: int, text: str) -> None:
    """Send a message via Telegram Bot API."""
    url = f"{TELEGRAM_API.format(token=token)}/sendMessage"
    html_text = md_to_html(text)

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.post(
            url,
            json={
                "chat_id": chat_id,
                "text": html_text,
                "parse_mode": "HTML",
            },
        )
        # Fallback to plain text if HTML parsing fails
        if resp.status_code != 200:
            await client.post(
                url,
                json={"chat_id": chat_id, "text": text},
            )


def md_to_html(text: str) -> str:
    """Convert basic markdown to Telegram-compatible HTML.

    Handles: **bold**, *italic*, `code`, ```code blocks```, [links](url)
    """
    # Preserve code blocks
    blocks: list[str] = []

    def save_block(m: re.Match) -> str:
        blocks.append(m.group(1))
        return f"%%CODEBLOCK{len(blocks) - 1}%%"

    text = re.sub(r"```(?:\w*\n)?(.*?)```", save_block, text, flags=re.DOTALL)

    # Escape HTML entities
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # Inline code
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)

    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)

    # Italic
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)

    # Links
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

    # Restore code blocks
    for i, block in enumerate(blocks):
        escaped = block.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"%%CODEBLOCK{i}%%", f"<pre>{escaped}</pre>")

    return text
