"""LLM-as-judge for instruction-adherence cases.

Used by suites where a quality signal can't be computed by regex (tone,
PII, structural organisation). The judge is intentionally fixed
(default ``claude-haiku-4.5``) and never the model under test, to keep
scoring independent.

Returns a 1-5 score plus a reason string. Token / cost telemetry is
included so the judge call itself can be aggregated into the run total.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from gbot.core.providers import litellm as llm_provider
from gbot_eval.capture import track_call

_JUDGE_PROMPT_VERSION = "v1"

_JUDGE_PROMPT = """You are an impartial evaluator. Score how well the
RESPONSE satisfies the INSTRUCTION on a 1-5 scale.

Scale:
1 — completely fails the instruction
2 — significant violation
3 — partial compliance
4 — meets the instruction with minor gaps
5 — fully compliant

Respond with strict JSON:
{{"score": 1-5, "reason": "<one short sentence>"}}

INSTRUCTION:
{instruction}

RESPONSE:
{response}
"""


@dataclass
class JudgeResult:
    score: int
    reason: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int
    error: str | None = None


async def judge(
    instruction: str,
    response: str,
    judge_model: str = "openrouter/anthropic/claude-haiku-4.5",
) -> JudgeResult:
    """Score a single response against a single instruction."""
    prompt = _JUDGE_PROMPT.format(
        instruction=instruction.strip(), response=response.strip()
    )
    call = await track_call(
        llm_provider.achat(
            messages=[{"role": "user", "content": prompt}],
            model=judge_model,
            temperature=0.0,
            max_tokens=200,
            response_format={"type": "json_object"},
        ),
        model=judge_model,
    )
    if call.error or not call.response:
        return JudgeResult(
            score=1,
            reason=f"judge failed: {call.error or 'no response'}",
            tokens_in=call.prompt_tokens,
            tokens_out=call.completion_tokens,
            cost_usd=call.cost_usd,
            latency_ms=call.latency_ms,
            error=call.error,
        )

    raw = (call.response.content or "").strip()
    try:
        parsed = json.loads(raw)
        score = int(parsed.get("score", 1))
        reason = str(parsed.get("reason", ""))[:200]
        score = max(1, min(5, score))
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        score, reason = 1, f"judge parse error: {e}"

    return JudgeResult(
        score=score,
        reason=reason,
        tokens_in=call.prompt_tokens,
        tokens_out=call.completion_tokens,
        cost_usd=call.cost_usd,
        latency_ms=call.latency_ms,
    )
