"""``memory.page_compile`` suite.

Renders the same ``_PAGE_PROMPT`` template the production
``EntityPageCompiler`` uses (``gbot/memory/entity_pages.py:47``) and
scores the resulting markdown against ground-truth expectations:

* keyword_coverage — entity descriptors mentioned (primary signal)
* hallucinations — banned terms absent (must be 0)
* bullet_count_ok / paragraph_words_ok — format adherence
* citation_recall — must-cite fact_ids appear in ``[fact_id:xxx]`` form
  (BONUS: the production prompt asks for citations but mainstream
  chat models rarely emit the literal ``[fact_id:...]`` token; we
  weight this lightly so realistic outputs don't score zero)

Aggregated quality is a weighted sum: keyword_coverage (40%) +
no-hallucination (30%) + format adherence (15%) + citation_recall
(15%).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from gbot.core.providers import litellm as llm_provider
from gbot.memory.entity_pages import _PAGE_PROMPT, EntityPageCompiler
from gbot_eval.capture import track_call
from gbot_eval.suites import register
from gbot_eval.suites._memory_helpers import load_fixture
from gbot_eval.suites._metrics import page_evaluation
from gbot_eval.suites.base import CaseResult, SuiteResult, sample_cases


class MemoryPageCompileSuite:
    name = "memory.page_compile"
    fixture_file = "memory_page_compile"

    async def run(self, model: str, sample_pct: int = 100) -> SuiteResult:
        cases = sample_cases(load_fixture(self.fixture_file), sample_pct)

        case_results: list[CaseResult] = []
        for case in cases:
            prompt = _render_prompt(case)
            call = await track_call(
                llm_provider.achat(
                    messages=[{"role": "user", "content": prompt}],
                    model=model,
                    temperature=0.2,
                    max_tokens=2000,
                ),
                model=model,
            )
            content_md = call.text
            scores = page_evaluation(case, content_md)
            quality = _composite_quality(scores)

            case_results.append(
                CaseResult(
                    case_id=case["case_id"],
                    quality=quality,
                    tokens_in=call.prompt_tokens,
                    tokens_out=call.completion_tokens,
                    latency_ms=call.latency_ms,
                    cost_usd=call.cost_usd,
                    error=call.error,
                    detail={
                        "entity": case["entity"],
                        "scores": scores,
                        "content_preview": content_md[:200],
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


def _render_prompt(case: dict) -> str:
    return _PAGE_PROMPT.format(
        entity=case["entity"],
        aliases=", ".join(case.get("aliases", [case["entity"]])),
        relations=EntityPageCompiler._format_relations(case.get("relations", [])),
        facts=EntityPageCompiler._format_facts(case.get("facts", [])),
    )


def _composite_quality(scores: dict[str, Any]) -> float:
    """Weighted blend of page evaluation signals.

    Keyword coverage carries the most weight because it captures the
    "did the page actually describe the entity" question. No-hallucination
    is the next biggest hammer (a page that invents facts is worse than
    a page missing some). Format adherence is light. Citation recall is
    a bonus — production models rarely emit the literal token.
    """
    keyword = float(scores.get("keyword_coverage", 0.0))
    no_hallu = 0.0 if scores.get("hallucinations") else 1.0
    fmt = (
        (1.0 if scores.get("bullet_count_ok") else 0.0)
        + (1.0 if scores.get("paragraph_words_ok") else 0.0)
    ) / 2.0
    citation = float(scores.get("citation_recall", 0.0))
    return 0.40 * keyword + 0.30 * no_hallu + 0.15 * fmt + 0.15 * citation


def _aggregate(cases: list[CaseResult]) -> dict[str, Any]:
    if not cases:
        return {}
    citation_recalls = [c.detail["scores"]["citation_recall"] for c in cases]
    keyword_covs = [c.detail["scores"]["keyword_coverage"] for c in cases]
    hallu_counts = [len(c.detail["scores"]["hallucinations"]) for c in cases]
    bullet_oks = [c.detail["scores"]["bullet_count_ok"] for c in cases]
    para_oks = [c.detail["scores"]["paragraph_words_ok"] for c in cases]
    latencies = sorted(c.latency_ms for c in cases)
    p95_idx = max(0, int(len(latencies) * 0.95) - 1)
    return {
        "quality": sum(citation_recalls) / len(citation_recalls),
        "citation_recall_mean": sum(citation_recalls) / len(citation_recalls),
        "keyword_coverage_mean": sum(keyword_covs) / len(keyword_covs),
        "hallucinations_total": sum(hallu_counts),
        "hallucination_cases": sum(1 for h in hallu_counts if h),
        "bullet_count_ok_rate": sum(bullet_oks) / len(bullet_oks),
        "paragraph_words_ok_rate": sum(para_oks) / len(para_oks),
        "tokens_in_avg": sum(c.tokens_in for c in cases) / len(cases),
        "tokens_out_avg": sum(c.tokens_out for c in cases) / len(cases),
        "tokens_total": sum(c.tokens_in + c.tokens_out for c in cases),
        "latency_ms_p50": latencies[len(latencies) // 2],
        "latency_ms_p95": latencies[p95_idx],
        "cost_total_usd": sum(c.cost_usd for c in cases),
        "cases_total": len(cases),
        "failures": sum(1 for c in cases if c.error),
    }


register(MemoryPageCompileSuite())
