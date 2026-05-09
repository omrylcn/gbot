"""Scoring rule registry.

Suite YAML cases declare scoring rules as ``[{"kind": "...", ...}, ...]``.
Each ``kind`` maps to a registered handler; ``run_scoring_rule`` dispatches.

Built-in handlers live in ``builtins.py``, the ``judge`` handler in
``judge.py``, and the Python escape hatch in ``expr.py``. New kinds
register themselves at import time via ``@register("kind_name")``.
"""

from __future__ import annotations

from gbot_eval.scoring.base import ScoringContext, ScoringHandler, ScoringResult

SCORING_REGISTRY: dict[str, ScoringHandler] = {}


def register(kind: str):
    """Decorator: ``@register("kind_name")``."""

    def decorator(fn: ScoringHandler) -> ScoringHandler:
        if kind in SCORING_REGISTRY:
            raise ValueError(f"duplicate scoring kind: {kind}")
        SCORING_REGISTRY[kind] = fn
        return fn

    return decorator


async def run_scoring_rule(
    rule: dict, ctx: ScoringContext
) -> ScoringResult:
    """Resolve and execute a single scoring rule from a suite YAML.

    Handlers may be either sync or async — this dispatcher awaits the
    result if needed. Lets cheap rules (regex, json) stay sync without
    boilerplate while the ``judge`` handler does its LLM call awaitably.
    """
    import asyncio

    kind = rule.get("kind")
    if not kind:
        return ScoringResult(score=0.0, error="missing 'kind' field")
    handler = SCORING_REGISTRY.get(kind)
    if handler is None:
        return ScoringResult(
            score=0.0, error=f"unknown scoring kind: {kind}"
        )
    try:
        result = handler(rule, ctx)
        if asyncio.iscoroutine(result):
            result = await result
        return result
    except Exception as e:  # pragma: no cover — defensive
        return ScoringResult(score=0.0, error=f"{type(e).__name__}: {e}")


def list_kinds() -> list[str]:
    return sorted(SCORING_REGISTRY.keys())


def _load_handlers() -> None:
    """Trigger import-time registration for every shipped handler."""
    from gbot_eval.scoring import builtins  # noqa: F401
    from gbot_eval.scoring import expr  # noqa: F401
    from gbot_eval.scoring import judge  # noqa: F401


_load_handlers()


__all__ = [
    "SCORING_REGISTRY",
    "ScoringContext",
    "ScoringHandler",
    "ScoringResult",
    "list_kinds",
    "register",
    "run_scoring_rule",
]
