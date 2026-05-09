"""Suite contract — every eval suite plugs in via this interface.

A suite is anything that takes a model, runs N cases against it, and
returns a structured ``SuiteResult``. The CLI / runner doesn't care
how a suite is implemented internally — it just registers it under
a unique name and asks ``run(model)``.

Two implementations live alongside each other during the YAML
migration:

* Legacy Python classes (one file per suite under ``suites/``) — kept
  until their YAML replacement lands and the file is deleted.
* ``YamlBackedSuite`` — a generic adapter that reads a YAML config
  and delegates per-case execution to a registered ``Runner``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
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


# ── YAML-backed suite adapter ───────────────────────────────────


@dataclass
class YamlBackedSuite:
    """Reads a YAML suite definition and delegates to a registered runner.

    All v1.22.0+ suites use this adapter — the YAML carries case data,
    the runner carries the LLM call shape and any gbot-specific bits.
    """

    config: dict[str, Any]
    yaml_path: Path | None = None

    @property
    def name(self) -> str:
        return self.config["name"]

    @property
    def fixture_file(self) -> str:
        return str(self.yaml_path) if self.yaml_path else self.name

    @property
    def runner_name(self) -> str:
        return self.config["runner"]

    @property
    def requires_gbot(self) -> bool:
        return bool(self.config.get("requires_gbot", False))

    async def run(self, model: str, sample_pct: int = 100) -> SuiteResult:
        # Late import to avoid circulars (runners imports suites.base
        # for CaseResult).
        from gbot_eval.runners import RUNNER_REGISTRY

        runner = RUNNER_REGISTRY.get(self.runner_name)
        if runner is None:
            raise RuntimeError(
                f"suite '{self.name}' wants runner '{self.runner_name}' "
                f"which isn't registered. Available: {sorted(RUNNER_REGISTRY)}"
            )

        cases = sample_cases(self.config.get("cases", []), sample_pct)
        results: list[CaseResult] = []
        for case in cases:
            cr = await runner.run_case(case, self.config, model)
            results.append(cr)

        return SuiteResult(
            name=self.name,
            model=model,
            ran_at=datetime.utcnow().isoformat(),
            cases=results,
            aggregate=_default_aggregate(results),
        )


def _default_aggregate(cases: list[CaseResult]) -> dict[str, Any]:
    """Standard aggregate every YamlBackedSuite produces.

    Quality = mean of per-case quality. Latency p50 / p95 from
    sorted ms list. Tokens + cost summed; failures counted.
    """
    if not cases:
        return {}
    qualities = [c.quality for c in cases]
    latencies = sorted(c.latency_ms for c in cases)
    p95_idx = max(0, int(len(latencies) * 0.95) - 1)
    return {
        "quality": sum(qualities) / len(qualities),
        "tokens_in_avg": sum(c.tokens_in for c in cases) / len(cases),
        "tokens_out_avg": sum(c.tokens_out for c in cases) / len(cases),
        "tokens_total": sum(c.tokens_in + c.tokens_out for c in cases),
        "latency_ms_p50": latencies[len(latencies) // 2],
        "latency_ms_p95": latencies[p95_idx],
        "cost_total_usd": sum(c.cost_usd for c in cases),
        "cases_total": len(cases),
        "failures": sum(1 for c in cases if c.error),
    }
