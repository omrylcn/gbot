"""OpenRouter provider — direct SDK, no LiteLLM adapter."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage
from loguru import logger
from openrouter import OpenRouter

from graphbot.core.providers.base import BaseLLMProvider


class OpenRouterLLM(BaseLLMProvider):
    """Direct OpenRouter SDK provider.

    Bypasses LiteLLM entirely — response_format, tools, and thinking
    parameters pass through without adapter interference.
    """

    def __init__(self, api_key: str) -> None:
        self._client = OpenRouter(api_key=api_key)

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
        """Send chat completion via OpenRouter SDK."""
        sdk_model = model.removeprefix("openrouter/")

        kwargs: dict[str, Any] = {
            "model": sdk_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if response_format:
            kwargs["response_format"] = response_format

        # Thinking mode: OpenRouter uses 'reasoning' parameter
        if thinking:
            kwargs["temperature"] = 1.0
            kwargs["reasoning"] = {"effort": "medium"}

        try:
            response = await self._client.chat.send_async(**kwargs)
            return self._to_ai_message(response)
        except Exception as e:
            logger.error(f"OpenRouter LLM error: {e}")
            return AIMessage(content=f"Error calling LLM: {e}")

    @staticmethod
    def _to_ai_message(response: Any) -> AIMessage:
        """Convert OpenRouter SDK response to LangChain AIMessage."""
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

        # OpenRouter returns 'reasoning' field; normalize to 'reasoning_content'
        additional_kwargs: dict[str, Any] = {}
        reasoning = getattr(msg, "reasoning", None) or getattr(
            msg, "reasoning_content", None
        )
        if reasoning:
            additional_kwargs["reasoning_content"] = reasoning

        return AIMessage(
            content=msg.content or "",
            tool_calls=tool_calls,
            additional_kwargs=additional_kwargs,
            response_metadata={
                "finish_reason": getattr(choice, "finish_reason", "stop") or "stop",
                "usage": {
                    "prompt_tokens": getattr(
                        response.usage, "prompt_tokens", 0
                    ) or 0,
                    "completion_tokens": getattr(
                        response.usage, "completion_tokens", 0
                    ) or 0,
                    "total_tokens": getattr(
                        response.usage, "total_tokens", 0
                    ) or 0,
                },
            },
        )
