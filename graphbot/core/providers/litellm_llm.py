"""LiteLLM provider — for non-OpenRouter models (openai/*, anthropic/*, etc.)."""

from __future__ import annotations

import json
import os
from typing import Any

import litellm
from langchain_core.messages import AIMessage
from loguru import logger

from graphbot.core.config.schema import Config
from graphbot.core.providers.base import BaseLLMProvider

litellm.suppress_debug_info = True


class LiteLLMLLM(BaseLLMProvider):
    """LiteLLM-backed provider for non-OpenRouter models."""

    def __init__(self, config: Config) -> None:
        self._setup_keys(config)

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
        if response_format:
            kwargs["response_format"] = response_format

        _is_moonshot = "moonshot" in model and "kimi" in model

        if thinking and _is_moonshot:
            kwargs["temperature"] = 1.0
            kwargs["extra_body"] = {
                "thinking": {"type": "enabled", "budget_tokens": 4096}
            }
        elif thinking:
            kwargs["temperature"] = 1.0
            kwargs["reasoning_effort"] = "medium"
        elif _is_moonshot:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

        try:
            response = await litellm.acompletion(**kwargs)
            return self._to_ai_message(response)
        except Exception as e:
            logger.error(f"LLM error: {e}")
            return AIMessage(content=f"Error calling LLM: {e}")

    async def asummarize(
        self,
        messages: list[dict[str, Any]],
        model: str = "openai/gpt-4o-mini",
        max_tokens: int = 500,
    ) -> str:
        """Summarize a conversation for session transition."""
        system_prompt = (
            "You are a conversation summarizer. Produce a concise summary in this format:\n\n"
            "First, write a brief narrative summary (2-4 sentences) capturing the main flow "
            "of the conversation, key decisions, and context.\n\n"
            "Then add structured bullets:\n"
            "- TOPICS: Main subjects discussed\n"
            "- DECISIONS: Choices made or preferences expressed\n"
            "- PENDING: Unresolved questions or next steps\n"
            "- USER_INFO: New personal information learned about the user\n\n"
            "Write in the same language as the conversation. "
            "Keep total output under 300 words. Skip sections with no content. "
            "Do NOT include greetings or filler."
        )
        summary_messages = [
            {"role": "system", "content": system_prompt},
            *messages,
            {"role": "user", "content": "Summarize this conversation concisely."},
        ]
        try:
            response = await litellm.acompletion(
                model=model,
                messages=summary_messages,
                temperature=0.3,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"Summarization failed: {e}")
            return ""

    async def aextract_facts(
        self,
        messages: list[dict[str, Any]],
        model: str = "openai/gpt-4o-mini",
        max_tokens: int = 300,
    ) -> dict[str, Any]:
        """Extract structured facts from a conversation."""
        system_prompt = (
            "Analyze this conversation and extract structured facts as JSON.\n"
            "Return a JSON object with these optional keys:\n"
            '- "preferences": user preferences as [{"key": "...", "value": "..."}]\n'
            '- "notes": important facts about the user as ["..."]\n\n'
            "Rules:\n"
            "- Only extract clearly stated facts, not assumptions\n"
            "- Preferences = explicit likes/dislikes/settings (e.g. language, style)\n"
            "- Notes = personal facts (job, interests, ongoing projects)\n"
            "- Skip greetings, filler, and technical tool details\n"
            "- Return {} if nothing worth extracting"
        )
        extraction_messages = [
            {"role": "system", "content": system_prompt},
            *messages,
            {"role": "user", "content": "Extract facts as JSON."},
        ]
        try:
            response = await litellm.acompletion(
                model=model,
                messages=extraction_messages,
                temperature=0.1,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content or "{}"
            return json.loads(raw)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"Fact extraction failed: {e}")
            return {}

    @staticmethod
    def _to_ai_message(response: Any) -> AIMessage:
        """Convert litellm response to LangChain AIMessage."""
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

        additional_kwargs: dict[str, Any] = {}
        reasoning = getattr(msg, "reasoning_content", None)
        if reasoning:
            additional_kwargs["reasoning_content"] = reasoning

        return AIMessage(
            content=msg.content or "",
            tool_calls=tool_calls,
            additional_kwargs=additional_kwargs,
            response_metadata={
                "finish_reason": choice.finish_reason or "stop",
                "usage": {
                    "prompt_tokens": getattr(response.usage, "prompt_tokens", 0),
                    "completion_tokens": getattr(
                        response.usage, "completion_tokens", 0
                    ),
                    "total_tokens": getattr(response.usage, "total_tokens", 0),
                },
            },
        )

    @staticmethod
    def _setup_keys(config: Config) -> None:
        """Set env vars for LiteLLM from config."""
        for env, val in [
            ("ANTHROPIC_API_KEY", config.providers.anthropic.api_key),
            ("OPENAI_API_KEY", config.providers.openai.api_key),
            ("OPENROUTER_API_KEY", config.providers.openrouter.api_key),
            ("DEEPSEEK_API_KEY", config.providers.deepseek.api_key),
            ("GROQ_API_KEY", config.providers.groq.api_key),
            ("GEMINI_API_KEY", config.providers.gemini.api_key),
            ("MOONSHOT_API_KEY", config.providers.moonshot.api_key),
        ]:
            if val:
                os.environ.setdefault(env, val)
