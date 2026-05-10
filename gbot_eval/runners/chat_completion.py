"""``chat_completion`` runner — single-turn prompt + scoring rules.

The most general runner: a YAML case carries a ``messages`` list (or
``task``+optional ``system``), ``available_tools``, optional
``response_format`` / ``max_tokens``, and a ``scoring`` rule list.
The runner makes a single ``llm_provider.achat`` call (with token /
latency / cost capture via ``track_call``), then dispatches each rule
through the scoring registry.

Quality is the mean of all rule scores by default. To use a non-mean
aggregate (weighted sum, composite formula), use ``kind: python`` in
the suite's last rule and return the composite directly.
"""

from __future__ import annotations

from typing import Any

from gbot.core.providers import llm as llm_provider
from gbot_eval.capture import reasoning_off_kwargs, track_call
from gbot_eval.runners import register
from gbot_eval.runners.base import Runner
from gbot_eval.scoring import ScoringContext, run_scoring_rule
from gbot_eval.suites.base import CaseResult


@register("chat_completion")
class ChatCompletionRunner(Runner):
    name = "chat_completion"

    async def run_case(
        self,
        case: dict,
        suite_config: dict,
        model: str,
    ) -> CaseResult:
        messages = self._build_messages(case)
        max_tokens = case.get(
            "max_tokens", suite_config.get("default_max_tokens", 600)
        )
        temperature = case.get(
            "temperature", suite_config.get("default_temperature", 0.1)
        )
        response_format = case.get("response_format") or suite_config.get(
            "default_response_format"
        )
        tools = self._build_tools(case, suite_config)

        kwargs: dict[str, Any] = {
            "messages": messages,
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            kwargs["response_format"] = response_format
        if tools:
            kwargs["tools"] = tools

        # Suite YAML can opt out: `disable_reasoning: false`. Default is
        # "auto" — only known reasoning models get the parameter.
        disable_mode = case.get(
            "disable_reasoning", suite_config.get("disable_reasoning", "auto")
        )
        kwargs.update(reasoning_off_kwargs(model, disable_mode))

        call = await track_call(llm_provider.achat(**kwargs), model=model)

        text = call.text
        tool_calls = (
            list(getattr(call.response, "tool_calls", []) or [])
            if call.response is not None
            else []
        )
        ctx = ScoringContext(text=text, tool_calls=tool_calls, call=call, case=case)

        rules = case.get("scoring", []) or []
        scoring_results = []
        for rule in rules:
            r = await run_scoring_rule(rule, ctx)
            scoring_results.append(r)

        if scoring_results:
            quality = sum(r.score for r in scoring_results) / len(scoring_results)
        else:
            quality = 0.0

        # Aggregate judge cost into the case total — a judge call inside
        # a scoring rule isn't visible to track_call but its cost matters.
        extra_cost = sum(
            float(r.detail.get("judge_cost_usd", 0.0) or 0.0)
            for r in scoring_results
        )
        extra_tokens_in = sum(
            int(r.detail.get("judge_tokens_in", 0) or 0)
            for r in scoring_results
        )
        extra_tokens_out = sum(
            int(r.detail.get("judge_tokens_out", 0) or 0)
            for r in scoring_results
        )

        return CaseResult(
            case_id=case["id"],
            quality=quality,
            tokens_in=call.prompt_tokens + extra_tokens_in,
            tokens_out=call.completion_tokens + extra_tokens_out,
            latency_ms=call.latency_ms,
            cost_usd=call.cost_usd + extra_cost,
            error=call.error,
            detail={
                "scoring": [
                    {"kind": rule.get("kind"), "score": sr.score,
                     "detail": sr.detail, "error": sr.error}
                    for rule, sr in zip(rules, scoring_results)
                ],
                "preview": text[:200],
                "tool_calls": [tc.get("name") for tc in tool_calls],
            },
        )

    @staticmethod
    def _build_messages(case: dict) -> list[dict]:
        if "messages" in case:
            return list(case["messages"])
        # task + optional system shorthand
        msgs: list[dict] = []
        if "system" in case:
            msgs.append({"role": "system", "content": case["system"]})
        if "task" in case:
            msgs.append({"role": "user", "content": case["task"]})
        return msgs

    @staticmethod
    def _build_tools(case: dict, suite_config: dict) -> list[dict] | None:
        """Resolve ``available_tools`` (case) against ``runner_config.tools_catalog``
        (suite). Catalog is a dict {name: openai_function_def}.
        """
        names = case.get("available_tools") or case.get("tools")
        if not names:
            return None
        catalog = (suite_config.get("runner_config") or {}).get("tools_catalog")
        if isinstance(catalog, dict):
            return [catalog[n] for n in names if n in catalog]
        # If catalog isn't loaded (e.g. raw inline tools), assume names
        # are already full tool definitions.
        return [t for t in names if isinstance(t, dict)]
