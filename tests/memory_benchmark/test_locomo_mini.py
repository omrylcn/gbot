"""LOCOMO-mini benchmark suite — opt-in via @pytest.mark.benchmark.

Run:
    uv run pytest -m benchmark
    uv run pytest -m benchmark -s   # show stats output

CI: serializes results to ``benchmark_results.json`` for
version-to-version regression tracking.

This suite **always runs** — even without an API key (degraded mode,
zero-vector embeddings). The unit tests below verify pipeline
mechanics; the live-API recall numbers are only meaningful when
``OPENROUTER_API_KEY`` is set.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.memory_benchmark.metrics import (
    QueryResult,
    aggregate,
    format_report,
    mrr,
    recall_at_k,
)
from tests.memory_benchmark.runner import BenchmarkConfig, run_benchmark


# ── Pure-function tests (always-on, no benchmark mark) ─────────


def test_recall_at_k_full_match():
    assert recall_at_k(["f01", "f02"], ["f01", "f02", "f03"], k=5) == 1.0


def test_recall_at_k_partial():
    assert recall_at_k(["f01", "f02", "f03"], ["f01", "f99"], k=5) == pytest.approx(1 / 3)


def test_recall_at_k_no_match():
    assert recall_at_k(["f01"], ["f99", "f98"], k=5) == 0.0


def test_recall_at_k_empty_expected_returns_one():
    """Adversarial queries (no expected) score 1.0 by convention."""
    assert recall_at_k([], ["f99"], k=5) == 1.0


def test_recall_at_k_outside_top_k():
    """If expected fact is at rank 6 but k=5, recall is 0."""
    retrieved = ["a", "b", "c", "d", "e", "target"]
    assert recall_at_k(["target"], retrieved, k=5) == 0.0


def test_mrr_first_position():
    assert mrr(["f01"], ["f01", "f02"]) == 1.0


def test_mrr_third_position():
    assert mrr(["f01"], ["a", "b", "f01"]) == pytest.approx(1 / 3)


def test_mrr_no_match():
    assert mrr(["f01"], ["a", "b"]) == 0.0


def test_aggregate_basic():
    results = [
        QueryResult(qid="q1", qtype="single-hop", query="x",
                    expected=["f01"], retrieved=["f01"], distances=[0.1],
                    latency_ms=10.0, tokens=20),
        QueryResult(qid="q2", qtype="single-hop", query="y",
                    expected=["f02"], retrieved=["f99"], distances=[0.5],
                    latency_ms=20.0, tokens=15),
    ]
    stats = aggregate(results)
    assert stats["total"] == 2
    assert "single-hop" in stats["by_type"]
    assert stats["by_type"]["single-hop"]["recall@5"] == 0.5
    assert stats["overall"]["recall@5"] == 0.5


# ── Live benchmark (opt-in) ───────────────────────────────────


@pytest.mark.benchmark
def test_locomo_mini_full_run(tmp_path):
    """End-to-end run on real fixtures. Requires OPENROUTER_API_KEY for
    meaningful recall numbers; without it, falls back to zero-vector
    embeddings and only verifies the pipeline doesn't crash.

    Asserts:
      - Pipeline completes for all 25 queries.
      - With a real key, ``recall@10 ≥ 0.6`` overall (loose floor —
        baseline). Tighten as the suite grows.
      - p95 latency ≤ 5000 ms (very loose; embedding API can be slow).
    """
    cfg = BenchmarkConfig(db_path=str(tmp_path / "bench.db"))
    has_key = bool(os.environ.get("OPENROUTER_API_KEY"))

    out = run_benchmark(cfg)
    stats = out["stats"]

    # Mechanical assertions (always meaningful)
    assert stats["total"] == 25
    assert "single-hop" in stats["by_type"]
    assert "multi-hop" in stats["by_type"]
    assert "adversarial" in stats["by_type"]

    # Print for human-readable CI logs
    print()
    print(format_report(stats))

    # Persist for version-to-version comparison
    out_path = Path(__file__).parent / "last_run.json"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))

    # Quality floor — only enforce when embeddings are real
    if has_key:
        recall_10 = stats["overall"]["recall@10"]
        assert recall_10 >= 0.6, (
            f"recall@10 regression: {recall_10:.3f} < 0.6 floor. "
            f"See {out_path} for details."
        )
        # p95 latency check — loose because OpenRouter network can spike
        p95 = stats["overall"]["latency_ms_p95"]
        assert p95 <= 5000, f"p95 latency too high: {p95}ms"
    else:
        pytest.skip("OPENROUTER_API_KEY not set — quality assertions skipped")


@pytest.mark.benchmark
def test_locomo_mini_distance_gate_effect(tmp_path):
    """A/B: max_distance=None (no gate) vs default 0.45.

    Expectation: with the gate, we drop noise that's far in embedding
    space. recall@10 should not get worse (we keep all relevant matches);
    sometimes slightly improves on adversarial queries.
    """
    if not os.environ.get("OPENROUTER_API_KEY"):
        pytest.skip("Real embeddings required for distance-gate comparison")

    cfg_open = BenchmarkConfig(
        db_path=str(tmp_path / "bench_open.db"),
        max_distance=None,
    )
    cfg_gated = BenchmarkConfig(
        db_path=str(tmp_path / "bench_gated.db"),
        max_distance=0.45,
    )

    out_open = run_benchmark(cfg_open)
    out_gated = run_benchmark(cfg_gated)

    print()
    print("=== NO GATE ===")
    print(format_report(out_open["stats"]))
    print()
    print("=== GATE = 0.45 ===")
    print(format_report(out_gated["stats"]))

    open_recall = out_open["stats"]["overall"]["recall@10"]
    gated_recall = out_gated["stats"]["overall"]["recall@10"]
    # Gate should not hurt by more than 10 points
    assert gated_recall >= open_recall - 0.1, (
        f"Distance gate regression: open={open_recall} gated={gated_recall}"
    )
