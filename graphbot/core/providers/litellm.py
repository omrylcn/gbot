"""LLM provider facade — module-level functions delegating to strategy providers.

Preserves backward compatibility so callers can do:
    from graphbot.core.providers import litellm as llm_provider
    await llm_provider.achat(...)

Internally routes openrouter/* models to OpenRouter SDK (direct),
other models to LiteLLM.
"""

from __future__ import annotations

import os
from typing import Any

from langchain_core.messages import AIMessage
from loguru import logger

from graphbot.core.config.schema import Config
from graphbot.core.providers.base import BaseLLMProvider
from graphbot.core.providers.litellm_llm import LiteLLMLLM
from graphbot.core.providers.openrouter_llm import OpenRouterLLM

# Global provider instances — set once by setup_provider()
_main_provider: BaseLLMProvider | None = None
_fallback_provider: LiteLLMLLM | None = None


def setup_provider(config: Config) -> None:
    """Initialize global provider instances based on config. Call once at startup."""
    global _main_provider, _fallback_provider

    _fallback_provider = LiteLLMLLM(config)

    if config.assistant.model.startswith("openrouter/"):
        api_key = (
            config.providers.openrouter.api_key
            or os.environ.get("OPENROUTER_API_KEY", "")
        )
        if api_key:
            _main_provider = OpenRouterLLM(api_key=api_key)
            logger.info("LLM provider: OpenRouter SDK (direct)")
        else:
            _main_provider = _fallback_provider
            logger.warning("OpenRouter API key missing, falling back to LiteLLM")
    else:
        _main_provider = _fallback_provider
        logger.info("LLM provider: LiteLLM")


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
    """Route to the correct provider based on model prefix."""
    assert _main_provider is not None, "Call setup_provider() first"
    if model.startswith("openrouter/") and isinstance(_main_provider, OpenRouterLLM):
        return await _main_provider.achat(
            messages, model, tools, temperature, max_tokens,
            api_base, thinking, response_format,
        )
    assert _fallback_provider is not None
    return await _fallback_provider.achat(
        messages, model, tools, temperature, max_tokens,
        api_base, thinking, response_format,
    )


async def asummarize(
    messages: list[dict[str, Any]],
    model: str = "openai/gpt-4o-mini",
    max_tokens: int = 500,
) -> str:
    """Delegate summarization to LiteLLM provider."""
    assert _fallback_provider is not None, "Call setup_provider() first"
    return await _fallback_provider.asummarize(messages, model, max_tokens)


async def aextract_facts(
    messages: list[dict[str, Any]],
    model: str = "openai/gpt-4o-mini",
    max_tokens: int = 300,
) -> dict[str, Any]:
    """Delegate fact extraction to LiteLLM provider."""
    assert _fallback_provider is not None, "Call setup_provider() first"
    return await _fallback_provider.aextract_facts(messages, model, max_tokens)
