"""Bootstrap helpers — model resolution + provider setup.

Wraps ``gbot.core.config`` and ``gbot.core.providers.litellm`` so suites
don't have to know about gbot's internals.
"""

from __future__ import annotations

import os

from loguru import logger

from gbot.core.config.loader import load_config
from gbot.core.providers.litellm import setup_provider


def has_api_key() -> bool:
    """True iff at least one supported LLM-provider API key is set."""
    return any(
        os.environ.get(k)
        for k in (
            "OPENROUTER_API_KEY",
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
        )
    )


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
