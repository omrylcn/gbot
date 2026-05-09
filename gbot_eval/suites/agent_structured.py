"""``agent.structured`` suite — JSON output adherence quality.

Calls ``llm_provider.achat`` with ``response_format={"type":"json_object"}``
on each fixture case, then validates the response against
case-specific schema expectations: required keys, types, nested keys,
array length / element types, value ranges.
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

_TYPE_CHECKERS = {
    "str": lambda v: isinstance(v, str),
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "float": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "bool": lambda v: isinstance(v, bool),
    "dict": lambda v: isinstance(v, dict),
    "list": lambda v: isinstance(v, list),
}


class AgentStructuredSuite:
    name = "agent.structured"
    fixture_file = "agent_structured"

    async def run(self, model: str, sample_pct: int = 100) -> SuiteResult:
        cases = sample_cases(
            json.loads((FIXTURES_DIR / "agent_structured.json").read_text())["cases"],
            sample_pct,
        )

        case_results: list[CaseResult] = []
        for case in cases:
            call = await track_call(
                llm_provider.achat(
                    messages=case["messages"],
                    model=model,
                    temperature=0.1,
                    max_tokens=case.get("max_tokens", 400),
                    response_format={"type": "json_object"},
                ),
                model=model,
            )

            text = ""
            if call.response is not None:
                text = (call.response.content or "").strip()
                if not text:
                    text = (
                        (getattr(call.response, "additional_kwargs", {}) or {}).get(
                            "reasoning_content", ""
                        )
                        or ""
                    ).strip()

            quality, detail = _score(case, text)
            case_results.append(
                CaseResult(
                    case_id=case["case_id"],
                    quality=quality,
                    tokens_in=call.prompt_tokens,
                    tokens_out=call.completion_tokens,
                    latency_ms=call.latency_ms,
                    cost_usd=call.cost_usd,
                    error=call.error,
                    detail={**detail, "preview": text[:160]},
                )
            )

        return SuiteResult(
            name=self.name,
            model=model,
            ran_at=datetime.utcnow().isoformat(),
            cases=case_results,
            aggregate=_aggregate(case_results),
        )


def _score(case: dict, text: str) -> tuple[float, dict]:
    """Each component scores 0/1; final = mean of applicable components."""
    detail: dict[str, Any] = {}
    parts: list[float] = []

    # Component 1: valid JSON object
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Lenient: try first {...} block
        import re

        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                detail["json_valid"] = False
                return 0.0, detail
        else:
            detail["json_valid"] = False
            return 0.0, detail
    if not isinstance(data, dict):
        detail["json_valid"] = True
        detail["root_is_object"] = False
        return 0.0, detail
    detail["json_valid"] = True
    parts.append(1.0)

    # Component 2: required top-level keys
    if "expected_keys" in case:
        missing = [k for k in case["expected_keys"] if k not in data]
        ok = not missing
        parts.append(1.0 if ok else 0.0)
        detail["keys"] = {"missing": missing, "ok": ok}

    # Component 3: type checks
    if "expected_types" in case:
        bad = []
        for k, t in case["expected_types"].items():
            checker = _TYPE_CHECKERS.get(t, lambda v: True)
            if k in data and not checker(data[k]):
                bad.append({"key": k, "want": t, "got": type(data[k]).__name__})
        ok = not bad
        parts.append(1.0 if ok else 0.0)
        detail["types"] = {"bad": bad, "ok": ok}

    # Component 4: literal field values
    if "expected_field_values" in case:
        bad = []
        for k, v in case["expected_field_values"].items():
            if str(data.get(k)) != str(v):
                bad.append({"key": k, "want": v, "got": data.get(k)})
        ok = not bad
        parts.append(1.0 if ok else 0.0)
        detail["values"] = {"bad": bad, "ok": ok}

    # Component 5: enum-like value sets
    if "expected_field_value_in" in case:
        bad = []
        for k, opts in case["expected_field_value_in"].items():
            if data.get(k) not in opts:
                bad.append({"key": k, "want_any": opts, "got": data.get(k)})
        ok = not bad
        parts.append(1.0 if ok else 0.0)
        detail["enums"] = {"bad": bad, "ok": ok}

    # Component 6: array min length
    if "expected_array_min_length" in case:
        bad = []
        for k, n in case["expected_array_min_length"].items():
            v = data.get(k)
            if not isinstance(v, list) or len(v) < n:
                bad.append({"key": k, "want_min": n, "got": len(v) if isinstance(v, list) else None})
        ok = not bad
        parts.append(1.0 if ok else 0.0)
        detail["array_min"] = {"bad": bad, "ok": ok}

    # Component 7: array max length
    if "expected_array_max_length" in case:
        bad = []
        for k, n in case["expected_array_max_length"].items():
            v = data.get(k)
            if isinstance(v, list) and len(v) > n:
                bad.append({"key": k, "want_max": n, "got": len(v)})
        ok = not bad
        parts.append(1.0 if ok else 0.0)
        detail["array_max"] = {"bad": bad, "ok": ok}

    # Component 8: array element type
    if "expected_array_element_type" in case:
        bad = []
        for k, t in case["expected_array_element_type"].items():
            v = data.get(k)
            checker = _TYPE_CHECKERS.get(t, lambda v: True)
            if isinstance(v, list):
                for elem in v:
                    if not checker(elem):
                        bad.append({"key": k, "want_each": t, "got": type(elem).__name__})
                        break
        ok = not bad
        parts.append(1.0 if ok else 0.0)
        detail["array_elem_type"] = {"bad": bad, "ok": ok}

    # Component 9: array element keys
    if "expected_array_element_keys" in case:
        bad = []
        for k, keys in case["expected_array_element_keys"].items():
            v = data.get(k)
            if isinstance(v, list):
                for i, elem in enumerate(v):
                    if not isinstance(elem, dict):
                        bad.append({"key": k, "elem_index": i, "issue": "not_dict"})
                        break
                    missing = [kk for kk in keys if kk not in elem]
                    if missing:
                        bad.append({"key": k, "elem_index": i, "missing": missing})
                        break
        ok = not bad
        parts.append(1.0 if ok else 0.0)
        detail["array_elem_keys"] = {"bad": bad, "ok": ok}

    # Component 10: nested keys
    if "expected_nested_keys" in case:
        bad = []
        for parent, children in case["expected_nested_keys"].items():
            v = data.get(parent)
            if not isinstance(v, dict):
                bad.append({"parent": parent, "issue": "not_dict"})
                continue
            missing = [c for c in children if c not in v]
            if missing:
                bad.append({"parent": parent, "missing": missing})
        ok = not bad
        parts.append(1.0 if ok else 0.0)
        detail["nested"] = {"bad": bad, "ok": ok}

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


register(AgentStructuredSuite())
