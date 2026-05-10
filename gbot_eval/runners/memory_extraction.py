"""``memory_extraction`` runner — gbot-bound.

Replicates the prompt shape of ``MemoryService._extract_typed_facts``
(``gbot/memory/extraction.py:411``). Each case is one synthetic
conversation; the model is asked to extract typed facts as JSON;
quality is fact-keyword recall.
"""

from __future__ import annotations

import json

try:
    from gbot.agent.profiles import get_agent_md
    _GBOT_AVAILABLE = True
except ImportError:
    get_agent_md = None  # type: ignore
    _GBOT_AVAILABLE = False

from gbot.core.providers import llm as llm_provider
from gbot_eval.capture import track_call
from gbot_eval.runners import register
from gbot_eval.runners.base import Runner
from gbot_eval.scoring.memory_metrics import (
    category_accuracy,
    extraction_recall,
    relation_recall,
)
from gbot_eval.suites.base import CaseResult


@register("memory_extraction")
class MemoryExtractionRunner(Runner):
    name = "memory_extraction"

    async def run_case(
        self,
        case: dict,
        suite_config: dict,
        model: str,
    ) -> CaseResult:
        if not _GBOT_AVAILABLE:
            raise RuntimeError(
                "memory_extraction runner needs gbot installed"
            )
        system_prompt = (get_agent_md("memory") if get_agent_md else "") or ""

        messages = [
            {"role": "system", "content": system_prompt},
            *case["conversation"],
            {
                "role": "user",
                "content": "Extract typed facts from this conversation as JSON.",
            },
        ]
        max_tokens = case.get(
            "max_tokens", suite_config.get("default_max_tokens", 4000)
        )
        call = await track_call(
            llm_provider.achat(
                messages=messages,
                model=model,
                temperature=case.get("temperature", 0.1),
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            ),
            model=model,
        )

        facts, relations, parse_error = _parse_facts(call)
        f_recall = extraction_recall(case.get("expected_facts", []), facts)
        r_recall = relation_recall(case.get("expected_relations", []), relations)
        c_acc = category_accuracy(case.get("expected_facts", []), facts)

        return CaseResult(
            case_id=case["id"],
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


def _parse_facts(call) -> tuple[list[dict], list[dict], str | None]:
    if call.error or not call.response:
        return [], [], call.error
    raw = call.text or '{"facts": []}'
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        import re

        match = re.search(r"\{.*\}", raw, re.DOTALL)
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
        f
        for f in data.get("facts", [])
        if isinstance(f, dict) and f.get("content")
    ]
    relations = [
        r
        for r in data.get("relations", [])
        if isinstance(r, dict)
        and r.get("source")
        and r.get("relation")
        and r.get("target")
    ]
    return facts, relations, None
