"""Messaging tools — send messages to other users."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.tools import tool
from loguru import logger

if TYPE_CHECKING:
    from graphbot.core.config.schema import Config
    from graphbot.memory.store import MemoryStore


def make_messaging_tools(config: Config, db: MemoryStore) -> list:
    """Create messaging tools for inter-user communication."""

    @tool
    def send_message_to_user(
        target_user: str,
        message: str,
        channel: str = "telegram",
    ) -> str:
        """Send a message to another user via their configured channel.

        Use this when the current user wants to send a direct message to another user.
        The message will be delivered via the target user's preferred channel (Telegram by default).

        Parameters
        ----------
        target_user : str
            The name or username of the recipient (e.g., "İhsan", "ihsan", "Zeynep", "zynp").
            The tool will search both user_id and name fields.
        message : str
            The message text to send.
        channel : str
            Channel to use (default: "telegram"). Auto-injected from context.

        Returns
        -------
        str
            Confirmation message or error.

        Examples
        --------
        - "İhsan'a 'Toplantı başlıyor' mesajı gönder"
        - "Send a message to Zeynep saying 'Hello!'"
        - "zynp'e 'Nasılsın?' yaz"
        """
        # Try to find user by user_id or name
        target_user_obj = db.get_user(target_user)

        if not target_user_obj:
            # Search by name
            all_users = db.list_users()
            matches = [
                u for u in all_users
                if u["name"] and u["name"].lower() == target_user.lower()
            ]

            if len(matches) == 0:
                return f"User '{target_user}' not found. Available users: {', '.join([u['name'] or u['user_id'] for u in all_users])}"
            elif len(matches) > 1:
                names = [f"{u['name']} ({u['user_id']})" for u in matches]
                return f"Multiple users found with name '{target_user}': {', '.join(names)}. Please use username instead."
            else:
                target_user_obj = matches[0]

        target_user_id = target_user_obj["user_id"]
        target_name = target_user_obj["name"] or target_user_id

        # Get channel link
        link = db.get_channel_link(target_user_id, channel)
        if not link:
            return f"User '{target_name}' has no {channel} channel configured."

        # Send via Telegram
        if channel == "telegram":
            chat_id = link["metadata"].get("chat_id")
            if not chat_id:
                return f"User '{target_name}' has not started their Telegram bot yet. They need to send /start first."

            try:
                import asyncio

                from graphbot.core.channels.telegram import send_message

                # Run async function in sync context
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(
                        send_message(link["channel_user_id"], int(chat_id), message)
                    )
                finally:
                    loop.close()

                logger.info(
                    f"Message sent to {target_name} ({target_user_id}) via {channel}: {message[:50]}"
                )
                return f"✓ Message sent to {target_name} via {channel}."

            except Exception as e:
                logger.error(f"Failed to send message to {target_name}: {e}")
                return f"Failed to send message to {target_name}: {e}"

        # Other channels (API, Discord, etc.) can be added here
        return f"Channel '{channel}' not supported for direct messaging yet."

    return [send_message_to_user]
