"""``agent.tool_calling`` suite — main agent's tool selection quality.

Calls ``llm_provider.achat(tools=...)`` directly with each case's
prompt and the case-specific subset of available tool definitions
(OpenAI function format, same shape ``gbot/agent/nodes.py:74`` builds).
Scores the resulting ``response.tool_calls`` against expectations.

Quality components (each case picks a subset that applies):

- ``expected_tool_names`` — required tool name(s) must appear in the call list
- ``forbidden_tool_names`` — these must NOT appear
- ``required_args`` — listed arg keys must be present on the matching call
- ``expected_arg_substring`` — case-insensitive substring on arg values
- ``expected_no_tool_call`` — model should answer in content, not tools
- ``expected_at_least_n_tools`` — multi-tool requests
- ``expected_any_of`` — list of acceptable tool-name sets
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from gbot.core.providers import litellm as llm_provider
from gbot_eval.capture import track_call
from gbot_eval.suites import register
from gbot_eval.suites.base import CaseResult, SuiteResult, sample_cases

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


class AgentToolCallingSuite:
    name = "agent.tool_calling"
    fixture_file = "agent_tool_calling"

    async def run(self, model: str, sample_pct: int = 100) -> SuiteResult:
        fixture = json.loads(
            (FIXTURES_DIR / "agent_tool_calling.json").read_text()
        )
        tool_defs = fixture["_tool_definitions"]
        cases = sample_cases(fixture["cases"], sample_pct)

        case_results: list[CaseResult] = []
        for case in cases:
            tools = [tool_defs[t] for t in case["tools"]]
            messages = [{"role": "user", "content": case["task"]}]
            call = await track_call(
                llm_provider.achat(
                    messages=messages,
                    model=model,
                    tools=tools,
                    temperature=0.1,
                    max_tokens=600,
                ),
                model=model,
            )

            tool_calls = []
            content = ""
            if call.response is not None:
                content = (call.response.content or "").strip()
                tool_calls = list(getattr(call.response, "tool_calls", []) or [])

            quality, detail = _score(case, tool_calls, content)
            case_results.append(
                CaseResult(
                    case_id=case["case_id"],
                    quality=quality,
                    tokens_in=call.prompt_tokens,
                    tokens_out=call.completion_tokens,
                    latency_ms=call.latency_ms,
                    cost_usd=call.cost_usd,
                    error=call.error,
                    detail={**detail, "content_preview": content[:120]},
                )
            )

        return SuiteResult(
            name=self.name,
            model=model,
            ran_at=datetime.utcnow().isoformat(),
            cases=case_results,
            aggregate=_aggregate(case_results),
        )


def _score(case: dict, tool_calls: list[dict], content: str) -> tuple[float, dict]:
    parts: list[float] = []
    detail: dict[str, Any] = {}
    called_names = [c.get("name") for c in tool_calls]
    detail["called"] = called_names

    if case.get("expected_no_tool_call"):
        ok = not tool_calls
        parts.append(1.0 if ok else 0.0)
        detail["no_tool_expected"] = {"ok": ok, "called": called_names}

    if "expected_tool_names" in case:
        expected = set(case["expected_tool_names"])
        hit = expected.issubset(set(called_names))
        parts.append(1.0 if hit else 0.0)
        detail["expected_tool_names"] = {"expected": list(expected), "ok": hit}

    if "forbidden_tool_names" in case:
        forb = set(case["forbidden_tool_names"])
        hit = not (forb & set(called_names))
        parts.append(1.0 if hit else 0.0)
        detail["forbidden"] = {"forbidden": list(forb), "ok": hit}

    if "expected_at_least_n_tools" in case:
        n_min = case["expected_at_least_n_tools"]
        ok = len(tool_calls) >= n_min
        parts.append(1.0 if ok else 0.0)
        detail["at_least_n"] = {"min": n_min, "got": len(tool_calls), "ok": ok}

    if "expected_any_of" in case:
        any_ok = any(
            set(opt).issubset(set(called_names))
            for opt in case["expected_any_of"]
        )
        parts.append(1.0 if any_ok else 0.0)
        detail["any_of"] = {"options": case["expected_any_of"], "ok": any_ok}

    if "required_args" in case:
        all_ok = True
        per_tool = {}
        for tname, keys in case["required_args"].items():
            for tc in tool_calls:
                if tc.get("name") != tname:
                    continue
                args = tc.get("args") or {}
                missing = [k for k in keys if k not in args]
                per_tool[tname] = {"missing": missing, "ok": not missing}
                if missing:
                    all_ok = False
                break
        parts.append(1.0 if all_ok else 0.0)
        detail["required_args"] = per_tool

    if "expected_arg_substring" in case:
        all_ok = True
        per_tool = {}
        for tname, kvs in case["expected_arg_substring"].items():
            for tc in tool_calls:
                if tc.get("name") != tname:
                    continue
                args = tc.get("args") or {}
                results = {}
                for k, v in kvs.items():
                    actual = str(args.get(k, "")).lower()
                    ok_kv = v.lower() in actual
                    results[k] = {"want": v, "got": str(args.get(k, ""))[:80], "ok": ok_kv}
                    if not ok_kv:
                        all_ok = False
                per_tool[tname] = results
                break
        parts.append(1.0 if all_ok else 0.0)
        detail["arg_substring"] = per_tool

    quality = sum(parts) / len(parts) if parts else 0.0
    return quality, detail


def _aggregate(cases: list[CaseResult]) -> dict[str, Any]:
    if not cases:
        return {}
    qualities = [c.quality for c in cases]
    latencies = sorted(c.latency_ms for c in cases)
    p95_idx = max(0, int(len(latencies) * 0.95) - 1)
    return {
        "quality": sum(qualities) / len(qualities),
        "tokens_in_avg": sum(c.tokens_in for c in cases) / len(cases),
        "tokens_out_avg": sum(c.tokens_out for c in cases) / len(cases),
        "tokens_total": sum(c.tokens_in + c.tokens_out for c in cases),
        "latency_ms_p50": latencies[len(latencies) // 2],
        "latency_ms_p95": latencies[p95_idx],
        "cost_total_usd": sum(c.cost_usd for c in cases),
        "cases_total": len(cases),
        "failures": sum(1 for c in cases if c.error),
    }


register(AgentToolCallingSuite())
