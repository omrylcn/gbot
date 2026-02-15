"""DelegationPlanner — single LLM call to plan subagent execution."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from loguru import logger

from graphbot.core.providers import litellm as llm_provider

if TYPE_CHECKING:
    from graphbot.core.config.schema import Config

_PLANNER_PROMPT = """\
You are a task delegation planner. Given a task description and available tools,
decide the optimal configuration for a background agent.

## Available Tools
{tool_catalog}

## Rules
- Pick ONLY the tools the agent actually needs for this task.
- Write a focused system prompt (2-3 sentences) for the agent.
- If the task is simple, suggest a cheaper model. If complex, suggest the main model.
- Return ONLY valid JSON, no markdown.

## Output Format (JSON)
{{
  "tools": ["tool_name_1", "tool_name_2"],
  "prompt": "You are a ... agent. Do X and return Y.",
  "model": "suggested model or null for default"
}}
"""


class DelegationPlanner:
    """Plan subagent execution with a single LLM call.

    Decides which tools, prompt, and model the subagent needs
    so the main agent only provides a task description.
    """

    def __init__(self, config: Config, tool_catalog: str) -> None:
        self.config = config
        self.tool_catalog = tool_catalog
        deleg = config.background.delegation
        self.model = deleg.model or config.assistant.model
        self.temperature = deleg.temperature

    async def plan(self, task: str) -> dict:
        """Plan delegation for a task.

        Returns
        -------
        dict
            ``{"tools": [...], "prompt": "...", "model": ...}``
        """
        system = _PLANNER_PROMPT.format(tool_catalog=self.tool_catalog)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Task: {task}"},
        ]
        response = await llm_provider.achat(
            messages=messages,
            model=self.model,
            temperature=self.temperature,
            max_tokens=512,
            api_base=self.config.get_api_base(self.model),
        )
        return self._parse(response.content)

    def _parse(self, text: str) -> dict:
        """Parse JSON from LLM response. Fallback to defaults on failure."""
        try:
            clean = text.strip()
            # Handle markdown code blocks
            if "```" in clean:
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            data = json.loads(clean.strip())
            raw_model = data.get("model")
            # LLMs sometimes return the literal string "null" instead of JSON null
            model = raw_model if isinstance(raw_model, str) and raw_model not in ("null", "") else None
            return {
                "tools": data.get("tools") or [],
                "prompt": data.get("prompt") or "Complete the given task thoroughly.",
                "model": model,
            }
        except (json.JSONDecodeError, IndexError):
            logger.warning("DelegationPlanner: failed to parse LLM response, using defaults")
            return {
                "tools": ["web_search", "web_fetch"],
                "prompt": "Complete the given task thoroughly and return a clear result.",
                "model": None,
            }
