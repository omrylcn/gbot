"""LLM call telemetry capture.

Wraps every LLM call with tokens / latency / cost capture so suites
don't have to repeat the same boilerplate. Reads
``response.response_metadata["usage"]`` populated by
``gbot.core.providers.{openrouter_llm,litellm_llm}``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Awaitable

from loguru import logger

from gbot_eval.pricing import calc_cost


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
