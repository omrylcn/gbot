"""``memory_audn`` runner — gbot-bound.

Replicates ``MemoryService._audn_decide`` (``gbot/memory/extraction.py:294``).
One LLM call per case; quality is exact match on the action
(add / update / delete / noop).
"""

from __future__ import annotations

import json

try:
    from gbot.agent.profiles import get_agent_md
    _GBOT_AVAILABLE = True
except ImportError:
    get_agent_md = None  # type: ignore
    _GBOT_AVAILABLE = False

from gbot.core.providers import litellm as llm_provider
from gbot_eval.capture import track_call
from gbot_eval.runners import register
from gbot_eval.runners.base import Runner
from gbot_eval.suites.base import CaseResult


@register("memory_audn")
class MemoryAudnRunner(Runner):
    name = "memory_audn"

    async def run_case(
        self,
        case: dict,
        suite_config: dict,
        model: str,
    ) -> CaseResult:
        if not _GBOT_AVAILABLE:
            raise RuntimeError("memory_audn runner needs gbot installed")
        system_prompt = (get_agent_md("memory") if get_agent_md else "") or ""

        existing_str = "\n".join(
            f"- [{f['fact_id']}] {f['content']}"
            for f in case["existing_facts"]
        )
        user_prompt = (
            f"EXISTING facts:\n{existing_str}\n\n"
            f"NEW fact: {case['new_fact']}\n\n"
            "Compare and decide: ADD, UPDATE, or NOOP?"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        call = await track_call(
            llm_provider.achat(
                messages=messages,
                model=model,
                temperature=case.get("temperature", 0.1),
                max_tokens=case.get(
                    "max_tokens", suite_config.get("default_max_tokens", 2000)
                ),
                response_format={"type": "json_object"},
            ),
            model=model,
        )
        decision, parse_error = _parse_decision(call)
        expected = (case["expected_action"] or "").lower()
        actual = (decision.get("action") or "").lower()
        correct = actual == expected

        return CaseResult(
            case_id=case["id"],
            quality=1.0 if correct else 0.0,
            tokens_in=call.prompt_tokens,
            tokens_out=call.completion_tokens,
            latency_ms=call.latency_ms,
            cost_usd=call.cost_usd,
            error=call.error or parse_error,
            detail={
                "expected": expected,
                "actual": actual,
                "target_fact_id": decision.get("target_fact_id"),
            },
        )


def _parse_decision(call) -> tuple[dict, str | None]:
    if call.error or not call.response:
        return {"action": "add"}, call.error
    raw = call.text or '{"action": "add"}'
    try:
        decision = json.loads(raw)
    except json.JSONDecodeError:
        import re

        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return {"action": "add"}, "no_json_block"
        try:
            decision = json.loads(match.group(0))
        except json.JSONDecodeError as e:
            return {"action": "add"}, f"json_decode_error: {e}"
    action = decision.get("action", "add").lower()
    if action not in ("add", "update", "delete", "noop"):
        action = "add"
    decision["action"] = action
    return decision, None
