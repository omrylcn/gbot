"""Suite contract — every eval suite plugs in via this interface.

A suite is anything that takes a model, runs N cases against it, and
returns a structured ``SuiteResult``. The CLI / runner doesn't care
how a suite is implemented internally — it just registers it under
a unique name and asks ``run(model)``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass
class CaseResult:
    """Per-case score + cross-cutting telemetry."""

    case_id: str
    quality: float
    tokens_in: int
    tokens_out: int
    latency_ms: int
    cost_usd: float
    error: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SuiteResult:
    """One run of one suite against one model."""

    name: str
    model: str
    ran_at: str
    cases: list[CaseResult]
    aggregate: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite": self.name,
            "model": self.model,
            "ran_at": self.ran_at,
            "aggregate": self.aggregate,
            "cases": [c.to_dict() for c in self.cases],
        }


class Suite(Protocol):
    """A runnable evaluation suite.

    Implementations live under ``gbot_eval/suites/`` and register
    themselves in ``SUITE_REGISTRY``.
    """

    name: str
    fixture_file: str

    async def run(self, model: str, sample_pct: int = 100) -> SuiteResult: ...


def sample_cases(cases: list[Any], sample_pct: int) -> list[Any]:
    """Deterministic sub-sample of fixture cases.

    Takes the first ``ceil(N * pct/100)`` cases — fixtures are already
    ordered for diversity, so a prefix slice is representative without
    introducing run-to-run variance.

    Always returns at least 1 case (clamped) so 1% on a 5-case fixture
    still runs one case rather than zero.
    """
    if sample_pct >= 100 or not cases:
        return list(cases)
    n = max(1, (len(cases) * sample_pct + 99) // 100)
    return list(cases[:n])
