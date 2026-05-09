"""Scoring DSL — common types.

Each scoring rule in a suite YAML resolves to one ``ScoringResult``
through a registered handler. Rules are pure functions of ``(rule_dict,
ScoringContext)`` so they're trivially testable without LLM calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from gbot_eval.capture import CallResult


@dataclass
class ScoringContext:
    """Inputs available to every scoring rule."""

    text: str                   # call.text — reasoning fallback applied
    tool_calls: list[dict]      # AIMessage.tool_calls
    call: CallResult            # full telemetry incl. tokens, latency, cost
    case: dict                  # the case dict from the suite YAML


@dataclass
class ScoringResult:
    """One rule's outcome — score in [0,1] plus a debug-friendly detail."""

    score: float
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class ScoringHandler(Protocol):
    """A rule handler is a callable of ``(rule_dict, context) -> ScoringResult``."""

    def __call__(
        self, rule: dict[str, Any], ctx: ScoringContext
    ) -> ScoringResult: ...
