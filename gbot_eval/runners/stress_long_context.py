"""``stress_long_context`` runner — builds 30-turn dummy dialogs.

Each case names a needle + filler topic + needle position (early /
middle / late) and a query. The runner inflates this into a full
30-turn message list, sends it through the LLM, and scores the
response with the standard scoring DSL (typically ``substring_any``
with a Turkish fold).

Stub mode: pure LLM, no GraphRunner / memory layer. This measures
the raw model's long-context attention, not the production retrieval
pipeline.
"""

from __future__ import annotations

from gbot.core.providers import litellm as llm_provider
from gbot_eval.capture import reasoning_off_kwargs, track_call
from gbot_eval.runners import register
from gbot_eval.runners.base import Runner
from gbot_eval.scoring import ScoringContext, run_scoring_rule
from gbot_eval.suites.base import CaseResult


@register("stress_long_context")
class StressLongContextRunner(Runner):
    name = "stress_long_context"

    async def run_case(
        self,
        case: dict,
        suite_config: dict,
        model: str,
    ) -> CaseResult:
        messages = self._build_messages(case, suite_config)
        max_tokens = case.get(
            "max_tokens", suite_config.get("default_max_tokens", 200)
        )
        disable_mode = case.get(
            "disable_reasoning", suite_config.get("disable_reasoning", "auto")
        )
        extra = reasoning_off_kwargs(model, disable_mode)
        call = await track_call(
            llm_provider.achat(
                messages=messages,
                model=model,
                temperature=case.get("temperature", 0.0),
                max_tokens=max_tokens,
                **extra,
            ),
            model=model,
        )

        ctx = ScoringContext(
            text=call.text, tool_calls=[], call=call, case=case
        )
        rules = case.get("scoring", []) or []
        scoring_results = []
        for rule in rules:
            r = await run_scoring_rule(rule, ctx)
            scoring_results.append(r)

        if scoring_results:
            quality = sum(r.score for r in scoring_results) / len(scoring_results)
        else:
            quality = 0.0

        return CaseResult(
            case_id=case["id"],
            quality=quality,
            tokens_in=call.prompt_tokens,
            tokens_out=call.completion_tokens,
            latency_ms=call.latency_ms,
            cost_usd=call.cost_usd,
            error=call.error,
            detail={
                "position": case.get("position"),
                "scoring": [
                    {"kind": rule.get("kind"), "score": sr.score,
                     "detail": sr.detail, "error": sr.error}
                    for rule, sr in zip(rules, scoring_results)
                ],
                "preview": call.text[:160],
            },
        )

    @staticmethod
    def _build_messages(case: dict, suite_config: dict) -> list[dict]:
        needle = case["needle"]
        query = case["query"]
        position = case.get("position", "middle")
        rc = suite_config.get("runner_config") or {}
        filler_count = int(case.get("filler_count", rc.get("filler_count", 28)))
        filler_topics = case.get("filler_topics") or rc.get("filler_topics") or [
            "yapay zeka tarihi",
            "antik Yunan filozofları",
            "uzay keşifleri",
        ]
        system_prompt = case.get(
            "system",
            rc.get("system", "Sen kullanıcının asistanısın, kısa cevap ver."),
        )

        fillers: list[dict] = []
        for i in range(filler_count):
            topic = filler_topics[i % len(filler_topics)]
            fillers.append({
                "role": "user",
                "content": f"({topic} hakkında konuşalım, dolgu mesaj #{i+1}.)",
            })
            fillers.append({
                "role": "assistant",
                "content": f"({topic} ile ilgili kısa bir not.)",
            })

        if position == "early":
            idx = 0
        elif position == "late":
            idx = len(fillers) - 1
        else:
            idx = len(fillers) // 2
        fillers.insert(idx, {"role": "user", "content": needle})
        fillers.append({"role": "user", "content": query})

        return [{"role": "system", "content": system_prompt}] + fillers
