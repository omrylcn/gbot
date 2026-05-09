"""Eval suite for the entity-page compile LLM call (Faz 22E Step 5).

Renders the same ``_PAGE_PROMPT`` the production compiler uses, calls
the LLM directly, and scores the markdown against ground-truth
expectations (citations, keywords, length, hallucinations).

The DB is intentionally bypassed — this is an LLM-quality eval, not an
integration test of the compiler's I/O. Production wiring is covered
by ``tests/test_entity_pages.py``.

Run::

    uv run pytest tests/llm_eval/test_page_compile_eval.py -v

Skipped automatically if no provider API key is set.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from gbot.core.providers import litellm as llm_provider
from gbot.memory.entity_pages import _PAGE_PROMPT, EntityPageCompiler

from .eval_metrics import page_evaluation, summarize


def _render_prompt(case: dict) -> str:
    return _PAGE_PROMPT.format(
        entity=case["entity"],
        aliases=", ".join(case.get("aliases", [case["entity"]])),
        relations=EntityPageCompiler._format_relations(case.get("relations", [])),
        facts=EntityPageCompiler._format_facts(case.get("facts", [])),
    )


@pytest.mark.asyncio
async def test_page_compile_quality(
    page_cases: list[dict],
    llm_model: str,
):
    citation_recalls: list[float] = []
    keyword_coverages: list[float] = []
    hallucination_counts: list[int] = []
    bullet_oks: list[bool] = []
    paragraph_oks: list[bool] = []
    durations: list[float] = []
    per_case: list[dict] = []

    for case in page_cases:
        prompt = _render_prompt(case)

        start = time.monotonic()
        try:
            response = await llm_provider.achat(
                messages=[{"role": "user", "content": prompt}],
                model=llm_model,
                temperature=0.2,
                max_tokens=400,
            )
        except Exception as e:
            pytest.fail(f"{case['case_id']}: page compile crashed: {e}")
        durations.append(time.monotonic() - start)

        content_md = (response.content or "").strip()
        scores = page_evaluation(case, content_md)

        citation_recalls.append(scores["citation_recall"])
        keyword_coverages.append(scores["keyword_coverage"])
        hallucination_counts.append(len(scores["hallucinations"]))
        bullet_oks.append(scores["bullet_count_ok"])
        paragraph_oks.append(scores["paragraph_words_ok"])

        per_case.append({
            "case_id": case["case_id"],
            "entity": case["entity"],
            "scores": scores,
            "duration_s": round(durations[-1], 2),
            "content_preview": content_md[:200],
        })

    report = {
        "model": llm_model,
        "citation_recall": summarize(citation_recalls),
        "keyword_coverage": summarize(keyword_coverages),
        "hallucinations": {
            "total": sum(hallucination_counts),
            "max_per_case": max(hallucination_counts) if hallucination_counts else 0,
            "cases_with_hallucination": sum(1 for c in hallucination_counts if c),
        },
        "bullet_count_ok_rate": (
            sum(bullet_oks) / len(bullet_oks) if bullet_oks else 0.0
        ),
        "paragraph_words_ok_rate": (
            sum(paragraph_oks) / len(paragraph_oks) if paragraph_oks else 0.0
        ),
        "latency_s": summarize(durations),
        "per_case": per_case,
    }

    out_dir = Path(__file__).parent
    (out_dir / "last_run_page_compile.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False)
    )

    print("\n" + json.dumps(report, indent=2, ensure_ascii=False))

    # Catastrophic-floor sanity — the LLM must at least produce text and
    # not hallucinate in every single case. Citation-format adherence and
    # keyword coverage are read from the JSON for cross-model review.
    non_empty = [c for c in per_case if c["content_preview"]]
    assert len(non_empty) >= len(page_cases) - 1, (
        f"model {llm_model} returned empty content for >1 case"
    )
    assert report["hallucinations"]["cases_with_hallucination"] < len(page_cases), (
        f"every page case had a hallucination — {llm_model} is unreliable"
    )
