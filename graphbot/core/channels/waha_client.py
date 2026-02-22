"""WAHA (WhatsApp HTTP API) client for GraphBot."""

from __future__ import annotations

import httpx
from loguru import logger


class WAHAClient:
    """Async client for WAHA REST API.

    Parameters
    ----------
    base_url : str
        WAHA server URL (e.g. "http://localhost:3000").
    session : str
        WAHA session name (default: "default").
    """

    def __init__(self, base_url: str, session: str = "default", api_key: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session
        self.api_key = api_key

    async def send_text(self, chat_id: str, text: str) -> dict:
        """Send a text message via WAHA.

        Parameters
        ----------
        chat_id : str
            WhatsApp chat ID (e.g. "905551234567@c.us").
        text : str
            Message text.
        """
        url = f"{self.base_url}/api/sendText"
        payload = {
            "session": self.session,
            "chatId": chat_id,
            "text": text,
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), headers=self._headers()) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code not in (200, 201):
                logger.warning(
                    f"WAHA sendText failed ({resp.status_code}): {resp.text[:200]}"
                )
            resp.raise_for_status()
            return resp.json()

    async def get_session_status(self) -> dict:
        """Get WAHA session status."""
        url = f"{self.base_url}/api/sessions/{self.session}"
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), headers=self._headers()) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()

    def _headers(self) -> dict[str, str]:
        """Build request headers with optional API key."""
        headers: dict[str, str] = {}
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
        return headers

    @staticmethod
    def phone_to_chat_id(phone: str) -> str:
        """Convert phone number to WhatsApp chat ID.

        Parameters
        ----------
        phone : str
            Phone number (e.g. "+905551234567" or "905551234567").

        Returns
        -------
        str
            Chat ID (e.g. "905551234567@c.us").
        """
        clean = phone.replace("+", "").replace(" ", "").replace("-", "")
        return f"{clean}@c.us"

    @staticmethod
    def chat_id_to_phone(chat_id: str) -> str:
        """Extract phone number from WhatsApp chat ID.

        Parameters
        ----------
        chat_id : str
            Chat ID (e.g. "905551234567@c.us").

        Returns
        -------
        str
            Phone number (e.g. "905551234567").
        """
        return chat_id.split("@")[0]
