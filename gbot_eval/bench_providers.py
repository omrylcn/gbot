"""Provider benchmark — LiteLLM vs OpenRouter SDK head-to-head.

The point of this module is **not** to score memory pipeline quality
(that's what the suites under ``gbot_eval/suites/memory_*`` do). It's
to answer one operational question:

    Given the same prompt + same model, which provider returns better
    quality, faster, cheaper?

For every fixture case we run TWO calls — one through ``OpenRouterLLM``
direct SDK, one through ``LiteLLMLLM`` adapter — using the same
``model`` id. The matrix output is ``(model × provider × area)``.

Decision basis: the empirical numbers, not opinions.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from gbot.core.config.loader import load_config
from gbot.core.providers.litellm_llm import LiteLLMLLM
from gbot.core.providers.openrouter_llm import OpenRouterLLM
from gbot_eval.capture import track_call

# Reasoning models we want to evaluate with reasoning *off* — they
# default to thinking mode, which (a) burns max_tokens before producing
# the answer and (b) wraps JSON in prose. Switching it off via
# OpenRouter's `reasoning: {enabled: false}` parameter lets us measure
# their non-reasoning capability head-to-head with non-reasoning models.
REASONING_MODELS = (
    "moonshotai/kimi-k2",
    "minimax/minimax-m2",
    "deepseek/deepseek-r1",
)


def _is_reasoning_model(model: str) -> bool:
    short = model.removeprefix("openrouter/").lower()
    return any(prefix in short for prefix in REASONING_MODELS)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
OUTPUT_ROOT = Path(__file__).parent / "output" / "runs"


# ── Provider abstraction ────────────────────────────────────────


class _ProviderHandle:
    """Provider adapter that can pass reasoning controls down to the
    underlying SDK.

    The gbot ``OpenRouterLLM`` / ``LiteLLMLLM`` wrappers don't expose
    ``reasoning`` in their public ``achat`` signatures, and we don't
    want to change gbot core just for a benchmark. So this handle
    holds a reference to the wrapper *and* its underlying SDK client,
    bypassing the wrapper when we need non-default features
    (``reasoning={"enabled": false}``) and using the wrapper otherwise
    for parity with production.
    """

    def __init__(self, name: str, wrapper: Any):
        self.name = name
        self.wrapper = wrapper

    async def achat(
        self,
        *,
        model: str,
        messages: list[dict],
        reasoning_disabled: bool = False,
        **kwargs,
    ):
        if not reasoning_disabled:
            return await self.wrapper.achat(messages=messages, model=model, **kwargs)
        if self.name == "openrouter_sdk":
            return await self._call_openrouter_with_reasoning_off(model, messages, kwargs)
        return await self._call_litellm_with_reasoning_off(model, messages, kwargs)

    async def _call_openrouter_with_reasoning_off(
        self, model: str, messages: list[dict], kwargs: dict
    ):
        """Direct OpenRouter SDK call with reasoning disabled — bypasses
        the gbot wrapper because ``OpenRouterLLM.achat`` filters its
        kwargs and would drop ``reasoning``.
        """
        from langchain_core.messages import AIMessage

        client = self.wrapper._client  # OpenRouter SDK instance
        sdk_model = model.removeprefix("openrouter/")
        sdk_kwargs: dict[str, Any] = {
            "model": sdk_model,
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.1),
            "max_tokens": kwargs.get("max_tokens", 1000),
            "reasoning": {"effort": "none"},
        }
        if kwargs.get("tools"):
            sdk_kwargs["tools"] = kwargs["tools"]
            sdk_kwargs["tool_choice"] = "auto"
        if kwargs.get("response_format"):
            sdk_kwargs["response_format"] = kwargs["response_format"]
        try:
            response = await client.chat.send_async(**sdk_kwargs)
        except Exception as e:
            return AIMessage(content=f"Error calling LLM: {e}")
        return self.wrapper._to_ai_message(response)

    async def _call_litellm_with_reasoning_off(
        self, model: str, messages: list[dict], kwargs: dict
    ):
        """Direct LiteLLM acompletion with extra_body.reasoning — gbot's
        ``LiteLLMLLM.achat`` doesn't pass through ``extra_body`` so we
        have to call ``litellm.acompletion`` ourselves.
        """
        import litellm
        from langchain_core.messages import AIMessage

        call_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.1),
            "max_tokens": kwargs.get("max_tokens", 1000),
            "extra_body": {"reasoning": {"effort": "none"}},
        }
        if kwargs.get("tools"):
            call_kwargs["tools"] = kwargs["tools"]
            call_kwargs["tool_choice"] = "auto"
        if kwargs.get("response_format"):
            call_kwargs["response_format"] = kwargs["response_format"]
        try:
            response = await litellm.acompletion(**call_kwargs)
        except Exception as e:
            return AIMessage(content=f"Error calling LLM: {e}")
        # Mirror LiteLLMLLM._to_ai_message shape
        choice = response.choices[0]
        msg = choice.message
        usage_obj = getattr(response, "usage", None)
        usage = {
            "prompt_tokens": getattr(usage_obj, "prompt_tokens", 0) if usage_obj else 0,
            "completion_tokens": getattr(usage_obj, "completion_tokens", 0) if usage_obj else 0,
            "total_tokens": getattr(usage_obj, "total_tokens", 0) if usage_obj else 0,
        }
        tool_calls = []
        for tc in (getattr(msg, "tool_calls", None) or []):
            args = tc.function.arguments
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            tool_calls.append(
                {"id": tc.id, "name": tc.function.name, "args": args}
            )
        return AIMessage(
            content=msg.content or "",
            tool_calls=tool_calls,
            additional_kwargs={
                "reasoning_content": getattr(msg, "reasoning_content", "") or "",
            },
            response_metadata={
                "finish_reason": choice.finish_reason or "stop",
                "usage": usage,
            },
        )


def make_providers() -> list[_ProviderHandle]:
    """Construct one OpenRouter SDK and one LiteLLM provider, sharing
    the OPENROUTER_API_KEY environment variable.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY required for provider bench")

    cfg = load_config()
    return [
        _ProviderHandle("openrouter_sdk", OpenRouterLLM(api_key=api_key)),
        _ProviderHandle("litellm", LiteLLMLLM(cfg)),
    ]


# ── Long-context message builder ────────────────────────────────


def _build_long_context_messages(case: dict) -> list[dict]:
    tpl = case["messages_template"]
    needle = tpl["needle"]
    filler_count = int(tpl["filler_count"])
    topic = tpl["filler_topic"]
    query = tpl["query"]
    position = case.get("_needle_position", "early")

    fillers = [
        {
            "role": "user",
            "content": (
                f"({topic} hakkında konuşalım, dolgu mesaj #{i+1}.)"
            ),
        }
        for i in range(filler_count)
    ]
    # Splice needle into chosen position
    if position == "early":
        idx = 0
    elif position == "late":
        idx = len(fillers) - 1
    else:  # middle
        idx = len(fillers) // 2
    fillers.insert(idx, {"role": "user", "content": needle})
    fillers.append({"role": "user", "content": query})
    return fillers


# ── Per-case quality scoring ────────────────────────────────────


def _score_case(case: dict, response_text: str, tool_calls: list[dict]) -> tuple[float, dict]:
    """Return (quality_0_to_1, detail_dict)."""
    area = case["area"]
    text_lower = (response_text or "").lower()

    if area == "general":
        if "expected_substring" in case:
            exp = case["expected_substring"].lower()
            ok = exp in text_lower
            return (1.0 if ok else 0.0), {"matched": exp, "ok": ok}
        if "expected_substring_any" in case:
            for opt in case["expected_substring_any"]:
                if opt.lower() in text_lower:
                    return 1.0, {"matched": opt, "ok": True}
            return 0.0, {"matched": None, "ok": False}
        return 0.0, {"reason": "no expectation"}

    if area == "long_context":
        exp = case["needle_substring"].lower()
        ok = exp in text_lower
        return (1.0 if ok else 0.0), {"needle": exp, "ok": ok}

    if area == "tool_call":
        if case.get("expected_no_tool_call"):
            ok = not tool_calls
            return (1.0 if ok else 0.0), {"called": [c.get("name") for c in tool_calls], "should_call": False}
        expected = case["expected_tool_name"]
        forbidden = set(case.get("forbidden_tool_names", []))
        called_names = [c.get("name") for c in tool_calls]
        if expected not in called_names:
            return 0.0, {"called": called_names, "expected": expected, "ok": False}
        if any(n in forbidden for n in called_names):
            return 0.0, {"called": called_names, "expected": expected, "forbidden_hit": True}
        # Check arg substring requirement
        arg_check = case.get("expected_arg_substring", {})
        if arg_check:
            for tc in tool_calls:
                if tc.get("name") != expected:
                    continue
                args = tc.get("args") or {}
                for k, v in arg_check.items():
                    if v.lower() not in str(args.get(k, "")).lower():
                        return 0.5, {
                            "called": called_names,
                            "expected": expected,
                            "ok": False,
                            "arg_check_failed": k,
                        }
                break
        return 1.0, {"called": called_names, "expected": expected, "ok": True}

    if area == "structured":
        try:
            data = json.loads((response_text or "").strip())
        except json.JSONDecodeError as e:
            return 0.0, {"json_valid": False, "err": str(e)}
        if not isinstance(data, dict):
            return 0.2, {"json_valid": True, "type_ok": False}
        score_parts = [0.4]  # base for valid JSON object
        keys_ok = True
        for k in case.get("expected_json_keys", []):
            if k not in data:
                keys_ok = False
                break
        if keys_ok:
            score_parts.append(0.3)
        # Field values
        field_check = case.get("expected_field_values", {})
        field_ok = True
        for k, v in field_check.items():
            if data.get(k) != v:
                # Allow string '25' vs int 25
                if str(data.get(k)) != str(v):
                    field_ok = False
                    break
        if field_ok and field_check:
            score_parts.append(0.15)
        elif not field_check:
            score_parts.append(0.15)  # nothing to check
        # Array min lengths
        arr_check = case.get("expected_array_min_length", {})
        arr_ok = True
        for k, n in arr_check.items():
            if not isinstance(data.get(k), list) or len(data[k]) < n:
                arr_ok = False
                break
        if arr_ok:
            score_parts.append(0.075)
        # Nested keys
        nested_check = case.get("expected_nested_keys", {})
        nested_ok = True
        for parent, children in nested_check.items():
            if not isinstance(data.get(parent), dict):
                nested_ok = False
                break
            for c in children:
                if c not in data[parent]:
                    nested_ok = False
                    break
        if nested_ok:
            score_parts.append(0.075)
        return min(1.0, sum(score_parts)), {
            "json_valid": True,
            "keys_ok": keys_ok,
            "field_ok": field_ok,
            "array_ok": arr_ok,
            "nested_ok": nested_ok,
        }

    return 0.0, {"reason": "unknown area"}


# ── Main bench orchestrator ─────────────────────────────────────


@dataclass
class CellResult:
    model: str
    provider: str
    case_id: str
    area: str
    quality: float
    tokens_in: int
    tokens_out: int
    latency_ms: int
    cost_usd: float
    error: str | None
    detail: dict


def _load_cases() -> list[dict]:
    return json.loads(
        (FIXTURES_DIR / "bench_providers.json").read_text()
    )["cases"]


async def _run_one_case(
    handle: _ProviderHandle, model: str, case: dict
) -> CellResult:
    area = case["area"]
    kwargs: dict[str, Any] = {"model": model, "temperature": 0.1}

    if area == "long_context":
        kwargs["messages"] = _build_long_context_messages(case)
        kwargs["max_tokens"] = case.get("max_tokens", 200)
    else:
        kwargs["messages"] = case["messages"]
        kwargs["max_tokens"] = case.get("max_tokens", 500)

    if "tools" in case:
        kwargs["tools"] = case["tools"]
    if "response_format" in case:
        kwargs["response_format"] = case["response_format"]

    reasoning_disabled = _is_reasoning_model(model)
    call = await track_call(
        handle.achat(reasoning_disabled=reasoning_disabled, **kwargs),
        model=model,
    )

    response_text = ""
    tool_calls: list[dict] = []
    if call.response is not None:
        response_text = (
            (getattr(call.response, "content", "") or "").strip()
            or (
                (getattr(call.response, "additional_kwargs", {}) or {}).get(
                    "reasoning_content", ""
                )
                or ""
            ).strip()
        )
        tool_calls = list(getattr(call.response, "tool_calls", []) or [])

    quality, detail = _score_case(case, response_text, tool_calls)

    return CellResult(
        model=model,
        provider=handle.name,
        case_id=case["case_id"],
        area=area,
        quality=quality,
        tokens_in=call.prompt_tokens,
        tokens_out=call.completion_tokens,
        latency_ms=call.latency_ms,
        cost_usd=call.cost_usd,
        error=call.error,
        detail={**detail, "response_preview": response_text[:160]},
    )


async def run_bench(models: list[str]) -> dict[str, Any]:
    """Run every (model × provider × case). Returns matrix payload."""
    cases = _load_cases()
    providers = make_providers()
    cells: list[CellResult] = []

    for model in models:
        for provider in providers:
            logger.info(
                f"bench-providers: {model} via {provider.name} ({len(cases)} cases)"
            )
            for case in cases:
                start = time.monotonic()
                cell = await _run_one_case(provider, model, case)
                logger.debug(
                    f"  {case['case_id']:<8} q={cell.quality:.2f} "
                    f"tok={cell.tokens_in}/{cell.tokens_out} "
                    f"lat={cell.latency_ms}ms ${cell.cost_usd:.5f}"
                )
                cells.append(cell)
                _ = time.monotonic() - start

    return _aggregate(cells, models, [p.name for p in providers], cases)


def _aggregate(
    cells: list[CellResult],
    models: list[str],
    provider_names: list[str],
    cases: list[dict],
) -> dict[str, Any]:
    """Build the ``model × provider × area`` matrix + per-case detail."""
    _ = cases  # cases list reserved for future per-area sorting
    by_key: dict[tuple[str, str, str], list[CellResult]] = {}

    for cell in cells:
        key = (cell.model, cell.provider, cell.area)
        by_key.setdefault(key, []).append(cell)

    matrix: dict[str, Any] = {
        "ran_at": datetime.utcnow().isoformat(),
        "models": models,
        "providers": provider_names,
        "cells": {},
        "totals": {
            "cost_usd": sum(c.cost_usd for c in cells),
            "tokens": sum(c.tokens_in + c.tokens_out for c in cells),
            "calls": len(cells),
            "failures": sum(1 for c in cells if c.error),
        },
    }

    for (model, provider, area), group in by_key.items():
        latencies = sorted(c.latency_ms for c in group)
        p95_idx = max(0, int(len(latencies) * 0.95) - 1)
        node = matrix["cells"].setdefault(model, {}).setdefault(provider, {})
        node[area] = {
            "quality": sum(c.quality for c in group) / len(group),
            "tokens_in_avg": sum(c.tokens_in for c in group) / len(group),
            "tokens_out_avg": sum(c.tokens_out for c in group) / len(group),
            "tokens_total": sum(c.tokens_in + c.tokens_out for c in group),
            "latency_ms_p50": latencies[len(latencies) // 2],
            "latency_ms_p95": latencies[p95_idx],
            "cost_total_usd": sum(c.cost_usd for c in group),
            "cases": [c.case_id for c in group],
            "failures": sum(1 for c in group if c.error),
        }

    matrix["per_case"] = [
        {
            "model": c.model,
            "provider": c.provider,
            "case_id": c.case_id,
            "area": c.area,
            "quality": c.quality,
            "tokens_in": c.tokens_in,
            "tokens_out": c.tokens_out,
            "latency_ms": c.latency_ms,
            "cost_usd": c.cost_usd,
            "error": c.error,
            "detail": c.detail,
        }
        for c in cells
    ]

    # Compute per-model winner (provider with higher avg quality)
    winners: dict[str, dict] = {}
    for model in models:
        model_cells = [c for c in cells if c.model == model]
        provider_scores: dict[str, list[float]] = {}
        provider_costs: dict[str, float] = {}
        provider_latencies: dict[str, list[int]] = {}
        for c in model_cells:
            provider_scores.setdefault(c.provider, []).append(c.quality)
            provider_costs[c.provider] = provider_costs.get(c.provider, 0.0) + c.cost_usd
            provider_latencies.setdefault(c.provider, []).append(c.latency_ms)
        provider_summary = {}
        for prov, scores in provider_scores.items():
            lats = sorted(provider_latencies[prov])
            p95 = lats[max(0, int(len(lats) * 0.95) - 1)] if lats else 0
            provider_summary[prov] = {
                "quality_mean": sum(scores) / len(scores),
                "cost_total_usd": provider_costs[prov],
                "p95_ms": p95,
            }
        ordered = sorted(
            provider_summary.items(),
            key=lambda kv: kv[1]["quality_mean"],
            reverse=True,
        )
        winners[model] = {
            "ranking": [name for name, _ in ordered],
            "scores": provider_summary,
        }

    matrix["winners_by_model"] = winners
    return matrix


def write_bench_run(matrix: dict[str, Any]) -> Path:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S")
    run_dir = OUTPUT_ROOT / f"{timestamp}_bench-providers"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "matrix.json").write_text(
        json.dumps(matrix, indent=2, ensure_ascii=False)
    )
    return run_dir
