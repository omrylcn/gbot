"""Suite orchestration + run output writing.

Top-level flow:

1. ``run_all(model, filter_spec)`` resolves suites, runs them sequentially,
   returns ``list[SuiteResult]``.
2. ``write_run(results, root)`` persists everything under
   ``gbot_eval/output/runs/<timestamp>_<model>/`` — one JSON per suite,
   plus ``manifest.json`` and ``matrix.json``.

Sequential, not concurrent — keeps cost predictable and avoids
provider rate limits.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from gbot_eval.reporting import aggregate
from gbot_eval.suites import filter_suites
from gbot_eval.suites.base import SuiteResult

OUTPUT_ROOT = Path(__file__).parent / "output" / "runs"


def _slugify_model(model: str) -> str:
    return model.replace("/", "_").replace(":", "-")


async def run_all(
    model: str, filter_spec: str | None = None
) -> list[SuiteResult]:
    """Run every suite (or those matching ``filter_spec``) against ``model``."""
    suites = filter_suites(filter_spec)
    if not suites:
        raise RuntimeError(
            f"no suites matched filter '{filter_spec}'. "
            f"Run 'gbot-eval list' to see registered suites."
        )

    results: list[SuiteResult] = []
    for suite in suites:
        logger.info(f"gbot-eval: running suite '{suite.name}' on '{model}'")
        start = time.monotonic()
        try:
            result = await suite.run(model)
        except Exception as e:
            logger.error(f"suite '{suite.name}' crashed: {e}")
            raise
        elapsed_ms = int((time.monotonic() - start) * 1000)
        result.aggregate.setdefault("runtime_ms", elapsed_ms)
        results.append(result)

    return results


def write_run(
    results: list[SuiteResult], output_root: Path | None = None
) -> Path:
    """Persist a run; returns the run directory path."""
    root = output_root or OUTPUT_ROOT
    root.mkdir(parents=True, exist_ok=True)

    if not results:
        raise RuntimeError("no results to write")

    timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S")
    model = results[0].model
    run_dir = root / f"{timestamp}_{_slugify_model(model)}"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Per-suite JSONs
    for r in results:
        path = run_dir / f"{r.name.replace('.', '_')}.json"
        path.write_text(json.dumps(r.to_dict(), indent=2, ensure_ascii=False))

    # Aggregate matrix.json
    matrix = aggregate(results)
    (run_dir / "matrix.json").write_text(
        json.dumps(matrix, indent=2, ensure_ascii=False)
    )

    # Manifest.json
    manifest: dict[str, Any] = {
        "model": model,
        "ran_at": results[0].ran_at,
        "suite_count": len(results),
        "totals": matrix["totals"],
        "suites": [r.name for r in results],
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False)
    )

    logger.info(f"gbot-eval: run written to {run_dir}")
    return run_dir


def list_runs(output_root: Path | None = None) -> list[Path]:
    """All existing run directories, newest first."""
    root = output_root or OUTPUT_ROOT
    if not root.exists():
        return []
    return sorted(
        (p for p in root.iterdir() if p.is_dir()),
        key=lambda p: p.name,
        reverse=True,
    )


def load_run(run_dir: Path) -> dict[str, Any]:
    """Read a run's matrix.json. Raises if missing."""
    matrix_path = run_dir / "matrix.json"
    if not matrix_path.exists():
        raise FileNotFoundError(f"no matrix.json in {run_dir}")
    return json.loads(matrix_path.read_text())
