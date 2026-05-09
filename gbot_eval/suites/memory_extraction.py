"""``memory.extraction`` suite.

Replicates the prompt shape of ``MemoryService._extract_typed_facts``
(see ``gbot/memory/extraction.py:411``) so we can wrap each call in
``track_call`` and capture per-case tokens / latency / cost.

Quality signal:

* fact_recall — ground-truth keyword hits in extracted fact contents
* relation_recall — expected (source, relation, target) recoverable
* category_accuracy — extracted facts had the expected category

Aggregated: ``quality = mean(fact_recall)``.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from loguru import logger

from gbot.agent.profiles import get_agent_md
from gbot.core.providers import litellm as llm_provider
from gbot_eval.capture import track_call
from gbot_eval.suites import register
from gbot_eval.suites._memory_helpers import load_fixture
from gbot_eval.suites._metrics import (
    category_accuracy,
    extraction_recall,
    relation_recall,
)
from gbot_eval.suites.base import CaseResult, SuiteResult, sample_cases


class MemoryExtractionSuite:
    name = "memory.extraction"
    fixture_file = "memory_extraction"

    async def run(self, model: str, sample_pct: int = 100) -> SuiteResult:
        cases = sample_cases(load_fixture(self.fixture_file), sample_pct)
        system_prompt = get_agent_md("memory") or ""
        if not system_prompt:
            logger.warning("memory.extraction: no agent prompt; suite degraded")

        case_results: list[CaseResult] = []
        for case in cases:
            messages = [
                {"role": "system", "content": system_prompt},
                *case["conversation"],
                {
                    "role": "user",
                    "content": "Extract typed facts from this conversation as JSON.",
                },
            ]
            call = await track_call(
                llm_provider.achat(
                    messages,
                    model=model,
                    temperature=0.1,
                    max_tokens=4000,
                    response_format={"type": "json_object"},
                ),
                model=model,
            )

            facts, relations, parse_error = _parse_facts(call)
            f_recall = extraction_recall(case.get("expected_facts", []), facts)
            r_recall = relation_recall(case.get("expected_relations", []), relations)
            c_acc = category_accuracy(case.get("expected_facts", []), facts)

            case_results.append(
                CaseResult(
                    case_id=case["case_id"],
                    quality=f_recall,
                    tokens_in=call.prompt_tokens,
                    tokens_out=call.completion_tokens,
                    latency_ms=call.latency_ms,
                    cost_usd=call.cost_usd,
                    error=call.error or parse_error,
                    detail={
                        "fact_recall": f_recall,
                        "relation_recall": r_recall,
                        "category_accuracy": c_acc,
                        "facts_extracted": len(facts),
                        "relations_extracted": len(relations),
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


def _parse_facts(call) -> tuple[list[dict], list[dict], str | None]:
    if call.error or not call.response:
        return [], [], call.error
    raw = call.text or '{"facts": []}'
    # Reasoning models occasionally wrap JSON in prose; try lenient extract.
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: find first {...} block.
        import re as _re
        match = _re.search(r"\{.*\}", raw, _re.DOTALL)
        if not match:
            return [], [], "no_json_block"
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as e:
            return [], [], f"json_decode_error: {e}"
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return [], [], "non_dict_response"
    facts = [
        f for f in data.get("facts", []) if isinstance(f, dict) and f.get("content")
    ]
    relations = [
        r
        for r in data.get("relations", [])
        if isinstance(r, dict) and r.get("source") and r.get("relation") and r.get("target")
    ]
    return facts, relations, None


def _aggregate(cases: list[CaseResult]) -> dict[str, Any]:
    if not cases:
        return {}
    qualities = [c.quality for c in cases]
    fact_recalls = [c.detail["fact_recall"] for c in cases]
    relation_recalls = [c.detail["relation_recall"] for c in cases]
    cat_accs = [c.detail["category_accuracy"] for c in cases]
    latencies = sorted(c.latency_ms for c in cases)
    p95_idx = max(0, int(len(latencies) * 0.95) - 1)
    return {
        "quality": sum(qualities) / len(qualities),
        "fact_recall_mean": sum(fact_recalls) / len(fact_recalls),
        "relation_recall_mean": sum(relation_recalls) / len(relation_recalls),
        "category_accuracy_mean": sum(cat_accs) / len(cat_accs),
        "tokens_in_avg": sum(c.tokens_in for c in cases) / len(cases),
        "tokens_out_avg": sum(c.tokens_out for c in cases) / len(cases),
        "tokens_total": sum(c.tokens_in + c.tokens_out for c in cases),
        "latency_ms_p50": latencies[len(latencies) // 2],
        "latency_ms_p95": latencies[p95_idx],
        "cost_total_usd": sum(c.cost_usd for c in cases),
        "cases_total": len(cases),
        "failures": sum(1 for c in cases if c.error),
    }


register(MemoryExtractionSuite())
