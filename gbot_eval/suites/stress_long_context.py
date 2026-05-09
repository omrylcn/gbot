"""``stress.long_context`` suite — needle-in-haystack on raw LLM.

Builds a 30-turn synthetic conversation per needle × position (early /
middle / late) and asks the model to recall a single fact buried among
filler turns. **Stub mode**: no GraphRunner, no memory layer, no
embedding retrieval — measures the raw model's long-context attention.
Production memory behaviour is covered by regression tests in
``tests/test_*.py``.

Quality = substring recall on the model's answer.
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


def _build_messages(
    needle: str, query: str, position: str, filler_count: int, filler_topics: list[str]
) -> list[dict]:
    fillers = []
    for i in range(filler_count):
        topic = filler_topics[i % len(filler_topics)]
        fillers.append({
            "role": "user",
            "content": f"({topic} hakkında konuşalım, dolgu mesaj #{i+1}.)",
        })
        fillers.append({
            "role": "assistant",
            "content": f"({topic} ile ilgili kısa bir not.)",
        })

    if position == "early":
        idx = 0
    elif position == "late":
        idx = len(fillers) - 1
    else:
        idx = len(fillers) // 2
    fillers.insert(idx, {"role": "user", "content": needle})
    fillers.append({"role": "user", "content": query})
    return [
        {"role": "system", "content": "Sen kullanıcının asistanısın, kısa cevap ver."},
    ] + fillers


class StressLongContextSuite:
    name = "stress.long_context"
    fixture_file = "stress_long_context"

    async def run(self, model: str, sample_pct: int = 100) -> SuiteResult:
        fixture = json.loads(
            (FIXTURES_DIR / "stress_long_context.json").read_text()
        )
        # Expand needles × positions into flat case list.
        all_cases = []
        for needle in fixture["needles"]:
            for pos in fixture["positions"]:
                all_cases.append({
                    "case_id": f"{needle['needle_id']}_{pos}",
                    "needle": needle["needle"],
                    "query": needle["query"],
                    "expected_substrings_any": needle["expected_substrings_any"],
                    "position": pos,
                })
        cases = sample_cases(all_cases, sample_pct)
        filler_count = fixture["_filler_count"]
        filler_topics = fixture["filler_topic_pool"]

        case_results: list[CaseResult] = []
        for case in cases:
            messages = _build_messages(
                case["needle"], case["query"], case["position"],
                filler_count, filler_topics,
            )
            call = await track_call(
                llm_provider.achat(
                    messages=messages,
                    model=model,
                    temperature=0.0,
                    max_tokens=200,
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
            text_lower = text.lower()

            hit = any(
                sub.lower() in text_lower for sub in case["expected_substrings_any"]
            )
            case_results.append(
                CaseResult(
                    case_id=case["case_id"],
                    quality=1.0 if hit else 0.0,
                    tokens_in=call.prompt_tokens,
                    tokens_out=call.completion_tokens,
                    latency_ms=call.latency_ms,
                    cost_usd=call.cost_usd,
                    error=call.error,
                    detail={
                        "position": case["position"],
                        "ok": hit,
                        "expected_any": case["expected_substrings_any"],
                        "preview": text[:160],
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


def _aggregate(cases: list[CaseResult]) -> dict[str, Any]:
    if not cases:
        return {}
    qualities = [c.quality for c in cases]
    by_pos: dict[str, list[float]] = {}
    for c in cases:
        by_pos.setdefault(c.detail["position"], []).append(c.quality)
    position_breakdown = {
        pos: sum(scores) / len(scores) for pos, scores in by_pos.items()
    }
    latencies = sorted(c.latency_ms for c in cases)
    p95_idx = max(0, int(len(latencies) * 0.95) - 1)
    return {
        "quality": sum(qualities) / len(qualities),
        "needle_recall": sum(qualities) / len(qualities),
        "position_breakdown": position_breakdown,
        "tokens_in_avg": sum(c.tokens_in for c in cases) / len(cases),
        "tokens_out_avg": sum(c.tokens_out for c in cases) / len(cases),
        "tokens_total": sum(c.tokens_in + c.tokens_out for c in cases),
        "latency_ms_p50": latencies[len(latencies) // 2],
        "latency_ms_p95": latencies[p95_idx],
        "cost_total_usd": sum(c.cost_usd for c in cases),
        "cases_total": len(cases),
        "failures": sum(1 for c in cases if c.error),
    }


register(StressLongContextSuite())
