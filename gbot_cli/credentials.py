"""Credential storage — ~/.graphbot/credentials.json."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_CRED_DIR = Path.home() / ".graphbot"
_CRED_FILE = _CRED_DIR / "credentials.json"


def load_credentials() -> dict[str, Any]:
    """Load saved credentials.

    Returns dict with optional keys: server_url, user_id, token, api_key.
    """
    if not _CRED_FILE.exists():
        return {}
    try:
        return json.loads(_CRED_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_credentials(data: dict[str, Any]) -> None:
    """Save credentials to disk (chmod 0600)."""
    _CRED_DIR.mkdir(parents=True, exist_ok=True)
    _CRED_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.chmod(_CRED_FILE, 0o600)


def clear_credentials() -> None:
    """Remove credentials file."""
    if _CRED_FILE.exists():
        _CRED_FILE.unlink()
