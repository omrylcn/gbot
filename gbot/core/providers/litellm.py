"""LLM provider facade — historical name kept for import-path stability.

The module name is ``litellm`` for backward compatibility — every caller
already does:

    from gbot.core.providers import litellm as llm_provider
    await llm_provider.achat(...)

Internally, OpenRouter SDK is the sole provider (decided 2026-05-09 via
``gbot-eval bench-providers`` head-to-head). The previous LiteLLM
fallback was dead code: production routes 100% through OpenRouter
because all configured ``model`` ids carry the ``openrouter/`` prefix.

To rename this module to ``llm.py`` later, update every
``from gbot.core.providers import litellm as llm_provider`` callsite in
one go — all callers already use the ``as llm_provider`` alias so the
rename is mechanical.
"""

from __future__ import annotations

import os
from typing import Any

from langchain_core.messages import AIMessage
from loguru import logger

from gbot.core.config.schema import Config
from gbot.core.providers.openrouter_llm import OpenRouterLLM

_provider: OpenRouterLLM | None = None


def setup_provider(config: Config) -> None:
    """Initialise the OpenRouter SDK provider. Call once at startup.

    Tolerant of missing keys at init time so unit tests / dev shells can
    import gbot without an API key in scope. The actual error surfaces
    when ``achat`` is called without a working provider.
    """
    global _provider
    api_key = (
        config.providers.openrouter.api_key
        or os.environ.get("OPENROUTER_API_KEY", "")
    )
    if not api_key:
        _provider = None
        logger.warning(
            "OPENROUTER_API_KEY missing — gbot LLM calls will fail until set."
        )
        return
    _provider = OpenRouterLLM(api_key=api_key)
    logger.info("LLM provider: OpenRouter SDK (direct)")


async def achat(
    messages: list[dict[str, Any]],
    model: str,
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    api_base: str | None = None,
    thinking: bool = False,
    response_format: dict[str, Any] | None = None,
) -> AIMessage:
    """Forward a chat completion request to the OpenRouter SDK provider."""
    if _provider is None:
        raise RuntimeError(
            "OpenRouter provider not initialised. Set OPENROUTER_API_KEY "
            "and call setup_provider(config) before issuing LLM calls."
        )
    return await _provider.achat(
        messages,
        model,
        tools,
        temperature,
        max_tokens,
        api_base,
        thinking,
        response_format,
    )
