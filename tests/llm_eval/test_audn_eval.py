"""Eval suite for the AUDN decision LLM call (Faz 22E Step 5).

Calls ``MemoryService._audn_decide`` directly with an existing-fact set
plus a new fact, scores ``action`` agreement against ground truth.

Run::

    uv run pytest tests/llm_eval/test_audn_eval.py -v

Skipped automatically if no provider API key is set.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from gbot.core.config.loader import load_config
from gbot.memory.extraction import MemoryService

from .eval_metrics import audn_accuracy, summarize


@pytest.fixture(scope="module")
def audn_service(llm_model: str) -> MemoryService:
    cfg = load_config()
    service = MemoryService.__new__(MemoryService)
    service.db = None  # _audn_decide doesn't touch the DB
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
async def test_audn_quality(
    audn_cases: list[dict],
    audn_service: MemoryService,
    llm_model: str,
):
    """Run all AUDN cases, aggregate exact-match accuracy + per-action."""
    decisions: list[dict] = []
    durations: list[float] = []

    for case in audn_cases:
        existing = case["existing_facts"]
        new_fact = {"content": case["new_fact"]}

        start = time.monotonic()
        try:
            decision = await audn_service._audn_decide(new_fact, existing)
        except Exception as e:
            pytest.fail(f"{case['case_id']}: AUDN crashed: {e}")
        durations.append(time.monotonic() - start)
        decisions.append(decision)

    metrics = audn_accuracy(audn_cases, decisions)
    report = {
        "model": llm_model,
        "accuracy": metrics["accuracy"],
        "correct": metrics["correct"],
        "total": metrics["total"],
        "per_action": metrics["per_action"],
        "confusions": metrics["confusions"],
        "latency_s": summarize(durations),
    }

    out_dir = Path(__file__).parent
    (out_dir / "last_run_audn.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False)
    )

    print("\n" + json.dumps(report, indent=2, ensure_ascii=False))

    # Catastrophic-floor sanity — random baseline over 4 actions is 0.25.
    # Anything above this is judged from the JSON output, not gated here.
    assert metrics["accuracy"] >= 0.3, (
        f"AUDN accuracy {metrics['accuracy']:.2f} < 0.3 — "
        f"model {llm_model} is at random-baseline. Confusions: "
        f"{metrics['confusions']}"
    )
