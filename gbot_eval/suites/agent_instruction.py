"""``agent.instruction`` suite — system-instruction adherence quality.

Two scoring mechanisms in one suite:

* ``_check: regex`` — deterministic checks (regex match/non-match,
  bullet count, word/sentence cap, JSON validity). Cheap, fast.
* ``_check: judge`` — sends prompt+response to a fixed judge model
  (``claude-haiku-4.5``) for a 1-5 score. More expensive; aggregated
  separately so cost lands in the right bucket.

Each case returns ``quality`` in [0,1]. For regex cases that's a hit
ratio across applied checks; for judge cases it's
``score / 5`` clamped to [0,1] with the floor (``judge_min_score / 5``)
treated as the bar.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from gbot.core.providers import litellm as llm_provider
from gbot_eval.capture import track_call
from gbot_eval.judge import judge as run_judge
from gbot_eval.suites import register
from gbot_eval.suites.base import CaseResult, SuiteResult, sample_cases

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

_SENTENCE_END = re.compile(r"[.!?]+(?:\s|$)")
_BULLET_LINE = re.compile(r"^\s*[-*•]\s+", re.MULTILINE)


class AgentInstructionSuite:
    name = "agent.instruction"
    fixture_file = "agent_instruction"

    async def run(self, model: str, sample_pct: int = 100) -> SuiteResult:
        cases = sample_cases(
            json.loads((FIXTURES_DIR / "agent_instruction.json").read_text())["cases"],
            sample_pct,
        )

        case_results: list[CaseResult] = []
        for case in cases:
            messages = [
                {"role": "system", "content": case["instruction"]},
                {"role": "user", "content": case["user_prompt"]},
            ]
            call = await track_call(
                llm_provider.achat(
                    messages=messages,
                    model=model,
                    temperature=0.3,
                    max_tokens=case.get("max_tokens", 400),
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

            check_kind = case.get("_check", "regex")
            judge_cost = 0.0
            judge_tokens_in = 0
            judge_tokens_out = 0

            if check_kind == "regex":
                quality, detail = _score_regex(case, text)
            else:
                jr = await run_judge(case["instruction"], text)
                judge_tokens_in = jr.tokens_in
                judge_tokens_out = jr.tokens_out
                judge_cost = jr.cost_usd
                quality = jr.score / 5.0
                detail = {
                    "judge_score": jr.score,
                    "judge_reason": jr.reason,
                    "min_score": case.get("judge_min_score", 4),
                    "ok": jr.score >= case.get("judge_min_score", 4),
                }

            case_results.append(
                CaseResult(
                    case_id=case["case_id"],
                    quality=quality,
                    tokens_in=call.prompt_tokens + judge_tokens_in,
                    tokens_out=call.completion_tokens + judge_tokens_out,
                    latency_ms=call.latency_ms,
                    cost_usd=call.cost_usd + judge_cost,
                    error=call.error,
                    detail={**detail, "check": check_kind, "preview": text[:200]},
                )
            )

        return SuiteResult(
            name=self.name,
            model=model,
            ran_at=datetime.utcnow().isoformat(),
            cases=case_results,
            aggregate=_aggregate(case_results),
        )


def _score_regex(case: dict, text: str) -> tuple[float, dict]:
    parts: list[float] = []
    detail: dict[str, Any] = {}

    if "regex_must_match" in case and case["regex_must_match"]:
        ok = bool(re.search(case["regex_must_match"], text))
        parts.append(1.0 if ok else 0.0)
        detail["must_match"] = {"pattern": case["regex_must_match"], "ok": ok}

    if "regex_must_not_match" in case and case["regex_must_not_match"]:
        ok = not re.search(case["regex_must_not_match"], text)
        parts.append(1.0 if ok else 0.0)
        detail["must_not_match"] = {"pattern": case["regex_must_not_match"], "ok": ok}

    if "bullet_count_exact" in case:
        n = len(_BULLET_LINE.findall(text))
        ok = n == case["bullet_count_exact"]
        parts.append(1.0 if ok else 0.0)
        detail["bullets"] = {"want": case["bullet_count_exact"], "got": n, "ok": ok}

    if "max_words" in case:
        n = len(text.split())
        ok = n <= case["max_words"]
        parts.append(1.0 if ok else 0.0)
        detail["max_words"] = {"limit": case["max_words"], "got": n, "ok": ok}

    if "max_sentences" in case:
        n = len(_SENTENCE_END.findall(text))
        ok = n <= case["max_sentences"]
        parts.append(1.0 if ok else 0.0)
        detail["max_sentences"] = {"limit": case["max_sentences"], "got": n, "ok": ok}

    if case.get("json_must_parse"):
        try:
            data = json.loads(text)
            ok = True
            for k in case.get("json_required_keys", []):
                if not isinstance(data, dict) or k not in data:
                    ok = False
                    break
        except json.JSONDecodeError:
            ok = False
            data = None
        parts.append(1.0 if ok else 0.0)
        detail["json"] = {"ok": ok}

    quality = sum(parts) / len(parts) if parts else 0.0
    return quality, detail


def _aggregate(cases: list[CaseResult]) -> dict[str, Any]:
    if not cases:
        return {}
    qualities = [c.quality for c in cases]
    regex_q = [c.quality for c in cases if c.detail.get("check") == "regex"]
    judge_q = [c.quality for c in cases if c.detail.get("check") == "judge"]
    latencies = sorted(c.latency_ms for c in cases)
    p95_idx = max(0, int(len(latencies) * 0.95) - 1)
    return {
        "quality": sum(qualities) / len(qualities),
        "regex_quality": sum(regex_q) / len(regex_q) if regex_q else None,
        "judge_quality": sum(judge_q) / len(judge_q) if judge_q else None,
        "tokens_in_avg": sum(c.tokens_in for c in cases) / len(cases),
        "tokens_out_avg": sum(c.tokens_out for c in cases) / len(cases),
        "tokens_total": sum(c.tokens_in + c.tokens_out for c in cases),
        "latency_ms_p50": latencies[len(latencies) // 2],
        "latency_ms_p95": latencies[p95_idx],
        "cost_total_usd": sum(c.cost_usd for c in cases),
        "cases_total": len(cases),
        "failures": sum(1 for c in cases if c.error),
    }


register(AgentInstructionSuite())
