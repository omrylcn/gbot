"""LiteLLM provider — thin wrapper that returns LangChain AIMessage."""

from __future__ import annotations

import json
import os
from typing import Any

import litellm
from langchain_core.messages import AIMessage
from loguru import logger

from graphbot.core.config.schema import Config

# Suppress litellm noise
litellm.suppress_debug_info = True


def setup_provider(config: Config) -> None:
    """Set env vars for LiteLLM from config. Call once at startup."""
    _set_key("ANTHROPIC_API_KEY", config.providers.anthropic.api_key)
    _set_key("OPENAI_API_KEY", config.providers.openai.api_key)
    _set_key("OPENROUTER_API_KEY", config.providers.openrouter.api_key)
    _set_key("DEEPSEEK_API_KEY", config.providers.deepseek.api_key)
    _set_key("GROQ_API_KEY", config.providers.groq.api_key)
    _set_key("GEMINI_API_KEY", config.providers.gemini.api_key)


async def achat(
    messages: list[dict[str, Any]],
    model: str,
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    api_base: str | None = None,
) -> AIMessage:
    """Call LiteLLM and return a LangChain AIMessage."""
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if api_base:
        kwargs["api_base"] = api_base

    try:
        response = await litellm.acompletion(**kwargs)
        return _to_ai_message(response)
    except Exception as e:
        logger.error(f"LLM error: {e}")
        return AIMessage(content=f"Error calling LLM: {e}")


def _to_ai_message(response: Any) -> AIMessage:
    """Convert litellm response → LangChain AIMessage."""
    choice = response.choices[0]
    msg = choice.message

    tool_calls = []
    if hasattr(msg, "tool_calls") and msg.tool_calls:
        for tc in msg.tool_calls:
            args = tc.function.arguments
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"raw": args}
            tool_calls.append(
                {"id": tc.id, "name": tc.function.name, "args": args}
            )

    return AIMessage(
        content=msg.content or "",
        tool_calls=tool_calls,
        response_metadata={
            "finish_reason": choice.finish_reason or "stop",
            "usage": {
                "prompt_tokens": getattr(response.usage, "prompt_tokens", 0),
                "completion_tokens": getattr(response.usage, "completion_tokens", 0),
                "total_tokens": getattr(response.usage, "total_tokens", 0),
            },
        },
    )


def _set_key(env_name: str, value: str) -> None:
    if value:
        os.environ.setdefault(env_name, value)
