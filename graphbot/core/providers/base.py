"""Base LLM provider — strategy pattern interface."""

from __future__ import annotations

import abc
from typing import Any

from langchain_core.messages import AIMessage


class BaseLLMProvider(abc.ABC):
    """Abstract base for LLM providers."""

    @abc.abstractmethod
    async def achat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        api_base: str | None = None,
        thinking: bool = False,
        response_format: dict[str, Any] | None = None,
    ) -> AIMessage:
        """Send a chat completion request and return an AIMessage."""
        ...
