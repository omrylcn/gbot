"""Bootstrap helpers — model resolution + provider setup.

Wraps ``gbot.core.config`` and ``gbot.core.providers.llm`` so suites
don't have to know about gbot's internals.
"""

from __future__ import annotations

import os

from loguru import logger

from gbot.core.config.loader import load_config
from gbot.core.providers.llm import setup_provider


def has_api_key() -> bool:
    """True iff an OpenRouter API key is reachable (env or config/.env).

    ``load_config()`` triggers Pydantic-Settings' .env merge so this
    works in CLI shells that don't have the env var exported globally.
    """
    if os.environ.get("OPENROUTER_API_KEY"):
        return True
    try:
        cfg = load_config()
        return bool(getattr(cfg.providers.openrouter, "api_key", "") or "")
    except Exception:
        return False


def resolve_model(explicit: str | None = None) -> str:
    """Pick the model under test.

    Precedence: explicit ``--model=`` flag → ``config.memory.model``
    → ``config.assistant.model``.
    """
    if explicit:
        return explicit
    cfg = load_config()
    return cfg.memory.model or cfg.assistant.model


def init_provider() -> None:
    """Initialise the LLM provider (LiteLLM or OpenRouter SDK)."""
    cfg = load_config()
    setup_provider(cfg)
    logger.debug("gbot-eval: LLM provider initialised")
