"""DelegationPlanner — single LLM call to plan execution strategy and processor."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from loguru import logger

from graphbot.core.providers import litellm as llm_provider

if TYPE_CHECKING:
    from graphbot.core.config.schema import Config

_PLANNER_PROMPT = """\
You are a task delegation planner. Given a task description and available tools,
decide the optimal execution strategy and configuration for a background agent.

## Available Tools
{tool_catalog}

## Two Orthogonal Decisions

### 1. Execution Type (WHEN to run)
- "immediate": Run now in background (research, computation, complex tasks)
- "delayed": Run once after a delay (send message later, check something later)
- "recurring": Run on a schedule (periodic checks, regular reports)
- "monitor": Run on a schedule, only notify when condition is met (price alerts)

### 2. Processor Type (HOW to run)
- "static": Send a plain text message to the user. No agent, no tool call. Use for simple reminders.
- "function": Call a specific tool with known arguments. No LLM needed. Use when the exact
  tool and arguments are clear (e.g. send a message to someone, add a favorite).
  The action itself is the goal — no result is sent back to the requesting user.
- "agent": Run a LightAgent (LLM + tools) for tasks requiring reasoning, interpretation,
  or multi-step work. The agent's output is sent to the user.

## Rules
- For "static": set tools=[], tool_name=null, tool_args=null, prompt=null.
- For "function": set tool_name and tool_args with the exact tool call. No prompt needed.
- For "agent": set tools list and a focused prompt (2-3 sentences) with full task details.
  ALWAYS include send_message_to_user in the tools list. The agent is responsible for delivering
  its own results. The prompt MUST instruct the agent to send results via send_message_to_user
  to the appropriate target user.
- If the task is simple, suggest a cheaper model. If complex, suggest the main model.
- For "delayed": estimate delay_seconds from the task description.
- For "recurring" and "monitor": produce a cron expression.
- For "monitor": the prompt MUST instruct the agent to respond with [SKIP] when nothing to report.
- Return ONLY valid JSON, no markdown.

## Examples
- "Remind me about the meeting in 2 hours"
  → execution: "delayed", processor: "static", delay_seconds: 7200,
    message: "Reminder: you have a meeting!"

- "Send a message to Murat saying hello in 5 minutes"
  → execution: "delayed", processor: "function", delay_seconds: 300,
    tool_name: "send_message_to_user",
    tool_args: {{"target_user": "Murat", "message": "hello"}}

- "Check the weather and report back in 2 minutes"
  → execution: "delayed", processor: "agent", delay_seconds: 120,
    tools: ["web_fetch", "send_message_to_user"],
    prompt: "Use web_fetch('weather:istanbul') to get current weather data, then send a detailed summary including temperature, humidity and wind."

- "Alert me when gold exceeds $3000"
  → execution: "monitor", processor: "agent", cron_expr: "*/30 * * * *",
    tools: ["web_fetch"],
    prompt: "Check gold price. If above $3000 report the current price. Otherwise [SKIP]."

- "Send hello to Zeynep every 10 minutes"
  → execution: "recurring", processor: "function", cron_expr: "*/10 * * * *",
    tool_name: "send_message_to_user",
    tool_args: {{"target_user": "Zeynep", "message": "hello"}}

- "Research this topic for me"
  → execution: "immediate", processor: "agent",
    tools: ["web_search", "web_fetch"],
    prompt: "Research the given topic thoroughly and return a clear summary."

- "Give me a weather report every morning at 9am"
  → execution: "recurring", processor: "agent", cron_expr: "0 9 * * *",
    tools: ["web_fetch", "send_message_to_user"],
    prompt: "Use web_fetch('weather:istanbul') to get current weather, then send a detailed report with temperature, humidity, wind speed."
{extra_examples}
## Output Format (JSON)
{{
  "execution": "immediate|delayed|recurring|monitor",
  "processor": "static|function|agent",
  "delay_seconds": null,
  "cron_expr": null,
  "message": null,
  "tool_name": null,
  "tool_args": null,
  "tools": [],
  "prompt": null,
  "model": null
}}
"""

_FALLBACK_PLAN: dict = {
    "execution": "immediate",
    "processor": "agent",
    "delay_seconds": None,
    "cron_expr": None,
    "message": None,
    "tool_name": None,
    "tool_args": None,
    "tools": ["web_search", "web_fetch"],
    "prompt": "Complete the given task thoroughly and return a clear result.",
    "model": None,
}

_VALID_EXECUTIONS = frozenset(("immediate", "delayed", "recurring", "monitor"))
_VALID_PROCESSORS = frozenset(("static", "function", "agent"))

_RESPONSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "delegation_plan",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "execution": {
                    "type": "string",
                    "enum": ["immediate", "delayed", "recurring", "monitor"],
                },
                "processor": {
                    "type": "string",
                    "enum": ["static", "function", "agent"],
                },
                "delay_seconds": {"type": ["integer", "null"]},
                "cron_expr": {"type": ["string", "null"]},
                "message": {"type": ["string", "null"]},
                "tool_name": {"type": ["string", "null"]},
                "tool_args": {},
                "tools": {"type": "array", "items": {"type": "string"}},
                "prompt": {"type": ["string", "null"]},
                "model": {"type": ["string", "null"]},
            },
            "required": [
                "execution", "processor", "delay_seconds", "cron_expr",
                "message", "tool_name", "tool_args", "tools", "prompt", "model",
            ],
            "additionalProperties": False,
        },
    },
}


class DelegationPlanner:
    """Plan execution strategy with a single LLM call.

    Makes two orthogonal decisions:
    1. Execution type (WHEN): immediate / delayed / recurring / monitor
    2. Processor type (HOW): static / function / agent
    """

    def __init__(self, config: Config, tool_catalog: str) -> None:
        self.config = config
        self.tool_catalog = tool_catalog
        deleg = config.background.delegation
        self.model = deleg.model or config.assistant.model
        self.temperature = deleg.temperature
        self._extra_examples = self._build_extra_examples(deleg.examples)

    @staticmethod
    def _build_extra_examples(examples: list[str]) -> str:
        """Format config-provided examples for prompt injection."""
        if not examples:
            return ""
        lines = ["\n## Additional Examples (from config)"]
        for ex in examples:
            lines.append(f"- {ex}")
        return "\n".join(lines) + "\n"

    async def plan(self, task: str) -> dict:
        """Plan delegation for a task.

        Returns
        -------
        dict
            Plan with execution type, processor type, and relevant config.
        """
        system = _PLANNER_PROMPT.format(
            tool_catalog=self.tool_catalog,
            extra_examples=self._extra_examples,
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Task: {task}"},
        ]
        logger.debug(f"Planner LLM call: model={self.model}, task={task[:60]}")
        response = await llm_provider.achat(
            messages=messages,
            model=self.model,
            temperature=self.temperature,
            max_tokens=512,
            api_base=self.config.get_api_base(self.model),
            response_format=_RESPONSE_SCHEMA,
        )
        content = response.content
        # Fallback: some thinking models put output in reasoning_content
        if not content or not content.strip():
            content = response.additional_kwargs.get("reasoning_content", "")
        logger.debug(f"Planner LLM raw: {content[:200]}")
        return self._parse(content)

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
            # Accept only valid litellm model strings (e.g. "openai/gpt-4o-mini")
            # Reject short/placeholder values like "main", "null", "default"
            model = None
            if isinstance(raw_model, str) and "/" in raw_model and len(raw_model) > 5:
                model = raw_model

            execution = data.get("execution", "immediate")
            if execution not in _VALID_EXECUTIONS:
                execution = "immediate"

            processor = data.get("processor", "agent")
            if processor not in _VALID_PROCESSORS:
                processor = "agent"

            return {
                "execution": execution,
                "processor": processor,
                "delay_seconds": data.get("delay_seconds"),
                "cron_expr": data.get("cron_expr"),
                "message": data.get("message"),
                "tool_name": data.get("tool_name"),
                "tool_args": data.get("tool_args"),
                "tools": data.get("tools") or [],
                "prompt": data.get("prompt"),
                "model": model,
            }
        except (json.JSONDecodeError, IndexError):
            logger.warning(
                "DelegationPlanner: failed to parse LLM response, using defaults"
            )
            return dict(_FALLBACK_PLAN)
