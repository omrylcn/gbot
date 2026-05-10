"""LLM call telemetry capture.

Wraps every LLM call with tokens / latency / cost capture so suites
don't have to repeat the same boilerplate. Reads
``response.response_metadata["usage"]`` populated by
``gbot.core.providers.openrouter_llm``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Awaitable

from loguru import logger

from gbot_eval.pricing import calc_cost

# Models that default to reasoning/thinking on; eval suites that want
# raw answer behaviour should opt in to ``disable_reasoning="auto"``.
# `bench_providers.py` had an earlier list; keeping it here so the
# capture layer can detect them on its own.
REASONING_MODEL_PREFIXES = (
    "moonshotai/kimi-k2",
    "minimax/minimax-m2",
    "deepseek/deepseek-r1",
)


def is_reasoning_model(model: str) -> bool:
    short = model.removeprefix("openrouter/").lower()
    return any(p in short for p in REASONING_MODEL_PREFIXES)


def reasoning_off_kwargs(
    model: str, mode: str | bool | None = "auto"
) -> dict[str, Any]:
    """No-op since v1.24.3 — the ``models.yaml`` registry now declares
    per-model thinking/reasoning defaults; ``OpenRouterLLM.achat``
    forwards them based on the looked-up profile rather than a runner-
    side override. Kept as a stub so existing runner imports keep
    working without a coordinated rename.

    ``mode`` is accepted for backward compatibility but ignored.
    """
    del model, mode  # unused — handled by the model registry now
    return {}


@dataclass
class CallResult:
    """Single LLM call telemetry + payload."""

    response: Any | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: int
    cost_usd: float
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        """Best-effort text — falls back to ``reasoning_content`` for
        thinking/reasoning models (Kimi-K2 family, DeepSeek-R1, etc.)
        whose final answer sometimes lands in ``additional_kwargs``
        when token limits run short. Mirrors the production fallback
        in ``gbot/agent/delegation.py:214``.
        """
        if not self.response:
            return ""
        content = (getattr(self.response, "content", "") or "").strip()
        if content:
            return content
        extras = getattr(self.response, "additional_kwargs", {}) or {}
        return (extras.get("reasoning_content") or "").strip()


async def track_call(
    coro: Awaitable[Any], model: str
) -> CallResult:
    """Run ``coro`` (an llm_provider.achat call) and capture telemetry.

    Never raises — wraps exceptions in ``CallResult.error`` so a single
    bad case doesn't tear down a whole suite run.
    """
    start = time.monotonic()
    try:
        response = await coro
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        logger.warning(f"track_call: LLM call failed ({model}): {e}")
        return CallResult(
            response=None,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            latency_ms=elapsed,
            cost_usd=0.0,
            error=str(e),
        )

    elapsed_ms = int((time.monotonic() - start) * 1000)
    metadata = getattr(response, "response_metadata", {}) or {}
    usage = metadata.get("usage", {}) or {}
    pt = int(usage.get("prompt_tokens", 0) or 0)
    ct = int(usage.get("completion_tokens", 0) or 0)
    return CallResult(
        response=response,
        prompt_tokens=pt,
        completion_tokens=ct,
        total_tokens=pt + ct,
        latency_ms=elapsed_ms,
        cost_usd=calc_cost(model, pt, ct),
        metadata=metadata,
    )
