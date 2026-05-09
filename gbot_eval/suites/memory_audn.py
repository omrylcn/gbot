"""``memory.audn`` suite — AUDN decision quality.

Replicates ``MemoryService._audn_decide`` (see
``gbot/memory/extraction.py:294``) so each call gets per-case telemetry.

Quality signal: exact-match accuracy on the ``action`` (add / update /
delete / noop). Per-action breakdown lands in the suite ``detail``.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from gbot.agent.profiles import get_agent_md
from gbot.core.providers import litellm as llm_provider
from gbot_eval.capture import track_call
from gbot_eval.suites import register
from gbot_eval.suites._memory_helpers import load_fixture
from gbot_eval.suites.base import CaseResult, SuiteResult, sample_cases


class MemoryAudnSuite:
    name = "memory.audn"
    fixture_file = "memory_audn"

    async def run(self, model: str, sample_pct: int = 100) -> SuiteResult:
        cases = sample_cases(load_fixture(self.fixture_file), sample_pct)
        system_prompt = get_agent_md("memory") or ""

        case_results: list[CaseResult] = []
        for case in cases:
            existing_str = "\n".join(
                f"- [{f['fact_id']}] {f['content']}" for f in case["existing_facts"]
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
                    messages,
                    model=model,
                    temperature=0.1,
                    max_tokens=2000,
                    response_format={"type": "json_object"},
                ),
                model=model,
            )
            decision, parse_error = _parse_decision(call)
            expected = (case["expected_action"] or "").lower()
            actual = (decision.get("action") or "").lower()
            correct = actual == expected

            case_results.append(
                CaseResult(
                    case_id=case["case_id"],
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
            )

        return SuiteResult(
            name=self.name,
            model=model,
            ran_at=datetime.utcnow().isoformat(),
            cases=case_results,
            aggregate=_aggregate(case_results),
        )


def _parse_decision(call) -> tuple[dict, str | None]:
    if call.error or not call.response:
        return {"action": "add"}, call.error
    raw = call.text or '{"action": "add"}'
    try:
        decision = json.loads(raw)
    except json.JSONDecodeError:
        import re as _re
        match = _re.search(r"\{.*\}", raw, _re.DOTALL)
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


def _aggregate(cases: list[CaseResult]) -> dict[str, Any]:
    if not cases:
        return {}
    correct = sum(1 for c in cases if c.quality > 0)
    per_action: dict[str, dict[str, int]] = {}
    confusions: list[dict[str, str]] = []
    for c in cases:
        exp = c.detail["expected"]
        act = c.detail["actual"]
        bucket = per_action.setdefault(exp, {"total": 0, "correct": 0})
        bucket["total"] += 1
        if exp == act:
            bucket["correct"] += 1
        else:
            confusions.append(
                {"case_id": c.case_id, "expected": exp, "actual": act}
            )
    latencies = sorted(c.latency_ms for c in cases)
    p95_idx = max(0, int(len(latencies) * 0.95) - 1)
    return {
        "quality": correct / len(cases),
        "accuracy": correct / len(cases),
        "correct": correct,
        "per_action": per_action,
        "confusions": confusions,
        "tokens_in_avg": sum(c.tokens_in for c in cases) / len(cases),
        "tokens_out_avg": sum(c.tokens_out for c in cases) / len(cases),
        "tokens_total": sum(c.tokens_in + c.tokens_out for c in cases),
        "latency_ms_p50": latencies[len(latencies) // 2],
        "latency_ms_p95": latencies[p95_idx],
        "cost_total_usd": sum(c.cost_usd for c in cases),
        "cases_total": len(cases),
        "failures": sum(1 for c in cases if c.error),
    }


register(MemoryAudnSuite())
