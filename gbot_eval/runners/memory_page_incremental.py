"""``memory_page_incremental`` runner — gbot-bound (Faz 22J-A).

Exercises the **incremental** compile prompt
(``_PAGE_PROMPT_INCREMENTAL`` in ``gbot/memory/entity_pages.py``)
that ships in v1.26.0+. Each case provides an existing four-section
page, a small set of delta facts, and assertions about what the
update must preserve / add / move to History.

Scoring (composite):
- 40% **verbatim_keep**  — old Lead/Profile substrings still appear
- 30% **delta_cited**    — every new fact_id appears as `[fact_id:xxx]`
- 15% **history_move**   — contradicted bullets end up in ## History
- 15% **section_intact** — all four `## Lead/Profile/Interactions/History`
                           headers present in the output

This isolates the wiki "update" semantics from the broader page-compile
quality measured by ``memory.page_compile``.
"""

from __future__ import annotations

import re
from typing import Any

try:
    from gbot.memory.entity_pages import (
        _PAGE_PROMPT_INCREMENTAL,
        EntityPageCompiler,
    )
    _GBOT_AVAILABLE = True
except ImportError:
    _PAGE_PROMPT_INCREMENTAL = ""
    EntityPageCompiler = None  # type: ignore
    _GBOT_AVAILABLE = False

from gbot.core.providers import llm as llm_provider
from gbot_eval.capture import track_call
from gbot_eval.runners import register
from gbot_eval.runners.base import Runner
from gbot_eval.suites.base import CaseResult


_FACT_ID_RE = re.compile(r"\[fact_id:([0-9a-fA-F]{6,16})\]")


@register("memory_page_incremental")
class MemoryPageIncrementalRunner(Runner):
    name = "memory_page_incremental"

    async def run_case(
        self,
        case: dict,
        suite_config: dict,
        model: str,
    ) -> CaseResult:
        if not _GBOT_AVAILABLE:
            raise RuntimeError(
                "memory_page_incremental runner needs gbot installed"
            )

        prompt = _PAGE_PROMPT_INCREMENTAL.format(
            entity=case["entity"],
            aliases=", ".join(case.get("aliases", [case["entity"]])),
            current_page=case.get("existing_page_md", ""),
            delta_facts=EntityPageCompiler._format_facts(
                case.get("delta_facts", [])
            ),
            context_facts=EntityPageCompiler._format_facts(
                case.get("context_facts", [])
            ),
            relations=EntityPageCompiler._format_relations(
                case.get("relations", [])
            ),
            budget_tokens=case.get(
                "budget_tokens", suite_config.get("default_max_tokens", 1500)
            ),
            today=case.get("today", "2026-05-13"),
        )

        call = await track_call(
            llm_provider.achat(
                messages=[{"role": "user", "content": prompt}],
                model=model,
                temperature=case.get("temperature", 0.2),
                max_tokens=case.get(
                    "max_tokens",
                    suite_config.get("default_max_tokens", 1500),
                ),
            ),
            model=model,
        )
        content_md = call.text

        scores = _score(case, content_md)
        quality = (
            0.40 * scores["verbatim_keep"]
            + 0.30 * scores["delta_cited"]
            + 0.15 * scores["history_move"]
            + 0.15 * scores["section_intact"]
        )

        return CaseResult(
            case_id=case["id"],
            quality=quality,
            tokens_in=call.prompt_tokens,
            tokens_out=call.completion_tokens,
            latency_ms=call.latency_ms,
            cost_usd=call.cost_usd,
            error=call.error,
            detail={
                **{f"score_{k}": v for k, v in scores.items()},
                "output": content_md[:1200],
            },
        )


def _score(case: dict, content_md: str) -> dict[str, float]:
    """All sub-scores in [0, 1]."""
    out: dict[str, float] = {
        "verbatim_keep": 0.0,
        "delta_cited": 0.0,
        "history_move": 0.0,
        "section_intact": 0.0,
    }
    text = content_md or ""
    lower = text.lower()

    # 1. verbatim_keep: every expected snippet appears unchanged.
    expected_verbatim = case.get("expected", {}).get("verbatim_substrings", [])
    if expected_verbatim:
        hits = sum(1 for s in expected_verbatim if s.lower() in lower)
        out["verbatim_keep"] = hits / len(expected_verbatim)
    else:
        out["verbatim_keep"] = 1.0  # no requirement → free pass

    # 2. delta_cited: every delta fact_id appears as [fact_id:xxx]
    cited = set(_FACT_ID_RE.findall(text))
    delta_ids = [f["fact_id"] for f in case.get("delta_facts", []) if f.get("fact_id")]
    if delta_ids:
        out["delta_cited"] = sum(1 for fid in delta_ids if fid in cited) / len(
            delta_ids
        )
    else:
        out["delta_cited"] = 1.0

    # 3. history_move: contradicted substrings land in ## History.
    expected_history = case.get("expected", {}).get("history_substrings", [])
    if expected_history:
        history_section = _section_text(content_md, "History")
        if history_section:
            hl = history_section.lower()
            hits = sum(1 for s in expected_history if s.lower() in hl)
            out["history_move"] = hits / len(expected_history)
        else:
            out["history_move"] = 0.0
    else:
        out["history_move"] = 1.0

    # 4. section_intact: all four canonical headers present.
    needed = ("Lead", "Profile", "Interactions", "History")
    found = sum(1 for h in needed if re.search(rf"^##\s+{h}\b", text, re.MULTILINE))
    out["section_intact"] = found / 4.0

    return out


def _section_text(md: str, header: str) -> str:
    """Extract the body under a given ## header up to the next ## header."""
    pattern = re.compile(
        rf"^##\s+{header}\s*$\n(.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(md or "")
    return m.group(1).strip() if m else ""
