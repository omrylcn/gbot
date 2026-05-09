"""``memory_page_compile`` runner — gbot-bound.

Renders the production ``_PAGE_PROMPT`` template
(``gbot/memory/entity_pages.py:47``) per case and scores the markdown
against keyword coverage, hallucination, format, and citation.

Quality is a composite — keyword 40%, no-hallu 30%, format 15%,
citation 15%. The previous suite reported quality=0 on every model
because mainstream chat LLMs rarely emit ``[fact_id:xxx]``; the
new composite reflects "did the page do its job" more honestly
while still penalising missing citations as a 15% slice.
"""

from __future__ import annotations

from typing import Any

try:
    from gbot.memory.entity_pages import _PAGE_PROMPT, EntityPageCompiler
    _GBOT_AVAILABLE = True
except ImportError:
    _PAGE_PROMPT = ""
    EntityPageCompiler = None  # type: ignore
    _GBOT_AVAILABLE = False

from gbot.core.providers import litellm as llm_provider
from gbot_eval.capture import track_call
from gbot_eval.runners import register
from gbot_eval.runners.base import Runner
from gbot_eval.scoring.memory_metrics import page_evaluation
from gbot_eval.suites.base import CaseResult


@register("memory_page_compile")
class MemoryPageCompileRunner(Runner):
    name = "memory_page_compile"

    async def run_case(
        self,
        case: dict,
        suite_config: dict,
        model: str,
    ) -> CaseResult:
        if not _GBOT_AVAILABLE:
            raise RuntimeError(
                "memory_page_compile runner needs gbot installed"
            )

        prompt = _PAGE_PROMPT.format(
            entity=case["entity"],
            aliases=", ".join(case.get("aliases", [case["entity"]])),
            relations=EntityPageCompiler._format_relations(
                case.get("relations", [])
            ),
            facts=EntityPageCompiler._format_facts(case.get("facts", [])),
        )
        call = await track_call(
            llm_provider.achat(
                messages=[{"role": "user", "content": prompt}],
                model=model,
                temperature=case.get("temperature", 0.2),
                max_tokens=case.get(
                    "max_tokens", suite_config.get("default_max_tokens", 2000)
                ),
            ),
            model=model,
        )
        content_md = call.text
        scores = page_evaluation(case, content_md)
        quality = _composite_quality(scores)

        return CaseResult(
            case_id=case["id"],
            quality=quality,
            tokens_in=call.prompt_tokens,
            tokens_out=call.completion_tokens,
            latency_ms=call.latency_ms,
            cost_usd=call.cost_usd,
            error=call.error,
            detail={
                "entity": case["entity"],
                "scores": scores,
                "preview": content_md[:200],
            },
        )


def _composite_quality(scores: dict[str, Any]) -> float:
    keyword = float(scores.get("keyword_coverage", 0.0))
    no_hallu = 0.0 if scores.get("hallucinations") else 1.0
    fmt = (
        (1.0 if scores.get("bullet_count_ok") else 0.0)
        + (1.0 if scores.get("paragraph_words_ok") else 0.0)
    ) / 2.0
    citation = float(scores.get("citation_recall", 0.0))
    return 0.40 * keyword + 0.30 * no_hallu + 0.15 * fmt + 0.15 * citation
