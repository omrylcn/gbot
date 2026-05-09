"""Eval suite for the fact-extraction LLM call (Faz 22E Step 5).

Calls ``MemoryService._extract_typed_facts`` directly with curated
conversations, then scores the model output against ground-truth
keywords and relations.

Run::

    uv run pytest tests/llm_eval/test_extraction_eval.py -v

To swap the model under test::

    uv run pytest tests/llm_eval/test_extraction_eval.py \\
        --model=openrouter/moonshotai/kimi-k2.6 -v

Skipped automatically if no provider API key is set.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from gbot.core.config.loader import load_config
from gbot.memory.extraction import MemoryService

from .eval_metrics import (
    category_accuracy,
    extraction_recall,
    relation_recall,
    summarize,
)


@pytest.fixture(scope="module")
def extraction_service(llm_model: str) -> MemoryService:
    """A MemoryService wired with the model under test.

    No DB is needed for ``_extract_typed_facts`` — it only does
    LLM calls + JSON parsing — so we pass ``db=None`` and the
    method works fine.
    """
    cfg = load_config()
    service = MemoryService.__new__(MemoryService)
    service.db = None  # _extract_typed_facts never reads it
    service.model = llm_model
    service.config = cfg.memory
    service.embedder = None
    service.resolver = None
    service.entity_compiler = None
    from gbot.agent.profiles import get_agent_md
    service._system_prompt = get_agent_md("memory") or ""
    service._update_model = llm_model
    return service


@pytest.mark.asyncio
async def test_extraction_quality(
    extraction_cases: list[dict],
    extraction_service: MemoryService,
    llm_model: str,
    request: pytest.FixtureRequest,
):
    """Run all 10 extraction cases, aggregate fact/relation recall."""
    fact_recalls: list[float] = []
    relation_recalls: list[float] = []
    category_accs: list[float] = []
    durations: list[float] = []
    per_case: list[dict] = []

    for case in extraction_cases:
        case_id = case["case_id"]
        messages = case["conversation"]
        expected_facts = case.get("expected_facts", [])
        expected_relations = case.get("expected_relations", [])
        min_facts = case.get("min_facts", 0)

        start = time.monotonic()
        try:
            facts, relations = await extraction_service._extract_typed_facts(messages)
        except Exception as e:
            pytest.fail(f"{case_id}: extraction crashed: {e}")
        duration = time.monotonic() - start

        # Sanity floor — extraction at least returns *some* facts where
        # ground truth has any. We don't gate on max_facts (null cases)
        # because models legitimately disagree on what counts as a fact;
        # over-extraction shows up in the JSON for cross-model review.
        if min_facts > 0:
            assert len(facts) >= 1, (
                f"{case_id}: ground truth requires facts but model extracted none"
            )

        f_rec = extraction_recall(expected_facts, facts)
        r_rec = relation_recall(expected_relations, relations)
        c_acc = category_accuracy(expected_facts, facts)

        fact_recalls.append(f_rec)
        relation_recalls.append(r_rec)
        category_accs.append(c_acc)
        durations.append(duration)

        per_case.append({
            "case_id": case_id,
            "fact_recall": f_rec,
            "relation_recall": r_rec,
            "category_accuracy": c_acc,
            "facts_extracted": len(facts),
            "relations_extracted": len(relations),
            "duration_s": round(duration, 2),
        })

    report = {
        "model": llm_model,
        "fact_recall": summarize(fact_recalls),
        "relation_recall": summarize(relation_recalls),
        "category_accuracy": summarize(category_accs),
        "latency_s": summarize(durations),
        "per_case": per_case,
    }

    # Persist a machine-readable run for cross-version comparison.
    out_dir = Path(__file__).parent
    (out_dir / "last_run_extraction.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False)
    )

    # Always print so devs see results in pytest -s.
    print("\n" + json.dumps(report, indent=2, ensure_ascii=False))

    # Catastrophic-floor sanity — anything above this is judged from
    # the JSON output, not asserted here. The point of this suite is
    # cross-model measurement, not pass/fail gating on a moving target.
    assert report["fact_recall"]["mean"] >= 0.2, (
        f"fact recall mean {report['fact_recall']['mean']:.2f} < 0.2 — "
        f"model {llm_model} is barely extracting anything"
    )
