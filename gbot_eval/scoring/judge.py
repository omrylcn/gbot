"""``kind: judge`` scoring rule — LLM-as-judge.

Wraps ``gbot_eval.judge.judge`` so it can be invoked declaratively from
a YAML suite. Async — the dispatcher awaits.

YAML usage:

    scoring:
      - kind: judge
        criteria: "Cevap profesyonel ve resmi tonda mı?"
        min_score: 4              # optional: pass threshold (default 4)
        judge_model: ...          # optional: override default judge

The handler returns ``ScoringResult.score = jr.score / 5`` so the rule
plays nicely with the rest of the DSL (which expects [0,1]).
"""

from __future__ import annotations

from gbot_eval.judge import judge as _judge
from gbot_eval.scoring import register
from gbot_eval.scoring.base import ScoringContext, ScoringResult


@register("judge")
async def _judge_kind(rule: dict, ctx: ScoringContext) -> ScoringResult:
    criteria = rule.get("criteria") or rule.get("instruction")
    if not criteria:
        return ScoringResult(
            score=0.0, error="missing 'criteria' (or 'instruction') field"
        )
    judge_model = rule.get(
        "judge_model", "openrouter/anthropic/claude-haiku-4.5"
    )
    min_score = int(rule.get("min_score", 4))

    try:
        jr = await _judge(criteria, ctx.text, judge_model)
    except Exception as e:
        return ScoringResult(score=0.0, error=f"judge failed: {e}")

    return ScoringResult(
        score=jr.score / 5.0,
        detail={
            "judge_score": jr.score,
            "min_score": min_score,
            "reason": jr.reason,
            "judge_tokens_in": jr.tokens_in,
            "judge_tokens_out": jr.tokens_out,
            "judge_cost_usd": jr.cost_usd,
            "ok": jr.score >= min_score,
        },
        error=jr.error,
    )
