"""``multi_turn`` runner — multi-turn conversational coherence.

Each case is a sequence of user turns; the runner threads conversation
state turn-by-turn, calling the LLM after every user message and
checking per-turn expectations:

* ``expect_contains_any`` — at least one of the listed substrings must
  appear in the response (with optional ``fold: turkish``)
* ``expect_not_contains`` — none of the listed substrings may appear

Per-turn score is the mean of these two checks (when applied); case
quality is the mean across turns. Tokens / latency / cost are summed
across turns; the case-level latency reported is the *total* dialog
duration, not a per-turn p95.
"""

from __future__ import annotations

import time
from typing import Any

from gbot.core.providers import litellm as llm_provider
from gbot_eval.capture import track_call
from gbot_eval.runners import register
from gbot_eval.runners.base import Runner
from gbot_eval.scoring.builtins import _fold
from gbot_eval.suites.base import CaseResult


@register("multi_turn")
class MultiTurnRunner(Runner):
    name = "multi_turn"

    async def run_case(
        self,
        case: dict,
        suite_config: dict,
        model: str,
    ) -> CaseResult:
        system = case.get("system") or suite_config.get(
            "default_system", "Sen kullanıcının asistanısın."
        )
        history: list[dict] = [{"role": "system", "content": system}]

        per_turn: list[dict[str, Any]] = []
        total_in = 0
        total_out = 0
        total_cost = 0.0
        start = time.monotonic()
        any_error: str | None = None

        for i, turn in enumerate(case.get("turns", [])):
            user_msg = turn.get("user") or turn.get("content")
            history.append({"role": "user", "content": user_msg})
            call = await track_call(
                llm_provider.achat(
                    messages=history,
                    model=model,
                    temperature=case.get("temperature", 0.2),
                    max_tokens=turn.get(
                        "max_tokens",
                        suite_config.get("default_max_tokens", 300),
                    ),
                ),
                model=model,
            )
            if call.error:
                any_error = call.error
            text = call.text
            history.append({"role": "assistant", "content": text})
            total_in += call.prompt_tokens
            total_out += call.completion_tokens
            total_cost += call.cost_usd

            score, detail = _score_turn(turn, text)
            per_turn.append({
                "turn_index": i,
                "user_preview": (user_msg or "")[:80],
                "response_preview": text[:160],
                "score": score,
                **detail,
            })

        case_quality = (
            sum(t["score"] for t in per_turn) / len(per_turn)
            if per_turn
            else 0.0
        )
        total_latency_ms = int((time.monotonic() - start) * 1000)

        return CaseResult(
            case_id=case["id"],
            quality=case_quality,
            tokens_in=total_in,
            tokens_out=total_out,
            latency_ms=total_latency_ms,
            cost_usd=total_cost,
            error=any_error,
            detail={
                "turns": per_turn,
                "n_turns": len(per_turn),
            },
        )


def _score_turn(turn: dict, text: str) -> tuple[float, dict[str, Any]]:
    fold = turn.get("fold")
    text_folded = _fold(text, fold) if fold else text.lower()
    parts: list[float] = []
    detail: dict[str, Any] = {}

    if "expect_contains_any" in turn:
        opts = [
            (_fold(v, fold) if fold else v.lower())
            for v in turn["expect_contains_any"]
        ]
        matched = next((v for v in opts if v in text_folded), None)
        ok = matched is not None
        parts.append(1.0 if ok else 0.0)
        detail["contains_any"] = {
            "matched": matched,
            "options": turn["expect_contains_any"],
            "ok": ok,
        }

    if "expect_not_contains" in turn:
        bad = [
            (_fold(v, fold) if fold else v.lower())
            for v in turn["expect_not_contains"]
        ]
        found = [v for v in bad if v in text_folded]
        ok = not found
        parts.append(1.0 if ok else 0.0)
        detail["not_contains"] = {
            "found": found,
            "forbidden": turn["expect_not_contains"],
            "ok": ok,
        }

    score = sum(parts) / len(parts) if parts else 1.0  # no checks → trivial pass
    return score, detail
