"""LOCOMO-mini benchmark metrics — recall@K, MRR, latency, tokens.

Pure functions; no I/O. Operate on lists of retrieved fact_ids vs the
ground-truth expected_fact_ids from queries.json.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass


@dataclass
class QueryResult:
    """One query's retrieval outcome."""

    qid: str
    qtype: str
    query: str
    expected: list[str]
    retrieved: list[str]
    distances: list[float]
    latency_ms: float
    tokens: int = 0  # context-injection token estimate (chars/4)


def recall_at_k(expected: Iterable[str], retrieved: Sequence[str], k: int) -> float:
    """recall@K — what fraction of expected facts appeared in the top-K
    retrieved.

    For adversarial queries (expected is empty), recall is defined as 1.0
    if the top-K returns no hit on any *unrelated* fact, else 0.0. We
    can't evaluate "no hit" without knowing what's irrelevant globally,
    so adversarial queries get a separate metric: ``adversarial_clean``.
    Here, an empty ``expected`` always returns 1.0 to keep the average
    sane; treat with care.
    """
    expected_set = {x for x in expected if x}
    if not expected_set:
        return 1.0
    top_k = set(retrieved[:k])
    hits = expected_set & top_k
    return len(hits) / len(expected_set)


def mrr(expected: Iterable[str], retrieved: Sequence[str]) -> float:
    """Mean Reciprocal Rank (MRR) — 1/rank of the first expected fact.

    If no expected fact appears, returns 0.0. For empty expected
    (adversarial), returns 1.0 (vacuously correct).
    """
    expected_set = {x for x in expected if x}
    if not expected_set:
        return 1.0
    for rank, fid in enumerate(retrieved, start=1):
        if fid in expected_set:
            return 1.0 / rank
    return 0.0


def adversarial_clean(expected: Iterable[str], retrieved: Sequence[str]) -> bool:
    """For adversarial queries (no expected), success = retrieval
    returned facts but none of them are highly-confident matches.

    Practical version: always returns True if expected is empty. This
    metric is a placeholder for future "hallucination check" — a real
    implementation needs LLM-judge or human review to decide whether the
    retrieved facts would actually mislead the agent.
    """
    expected_set = {x for x in expected if x}
    if expected_set:
        return False  # not an adversarial query
    return True


def aggregate(results: list[QueryResult], k_values: tuple[int, ...] = (5, 10)) -> dict:
    """Collapse a list of QueryResult into a stats dict.

    Per-type breakdown + global averages. Useful for CI artifact and
    version-to-version regression tracking.
    """
    by_type: dict[str, list[QueryResult]] = {}
    for r in results:
        by_type.setdefault(r.qtype, []).append(r)

    out: dict = {
        "total": len(results),
        "by_type": {},
        "overall": {},
    }

    for qtype, group in by_type.items():
        type_stats = {"count": len(group)}
        for k in k_values:
            recalls = [recall_at_k(r.expected, r.retrieved, k) for r in group]
            type_stats[f"recall@{k}"] = round(statistics.mean(recalls), 3)
        type_stats["mrr"] = round(
            statistics.mean(mrr(r.expected, r.retrieved) for r in group), 3
        )
        latencies = [r.latency_ms for r in group]
        type_stats["latency_ms_avg"] = round(statistics.mean(latencies), 1)
        type_stats["latency_ms_p95"] = round(
            statistics.quantiles(latencies, n=20)[-1] if len(latencies) >= 2
            else latencies[0],
            1,
        )
        out["by_type"][qtype] = type_stats

    # Overall (across all queries)
    for k in k_values:
        all_recalls = [recall_at_k(r.expected, r.retrieved, k) for r in results]
        out["overall"][f"recall@{k}"] = round(statistics.mean(all_recalls), 3)
    out["overall"]["mrr"] = round(
        statistics.mean(mrr(r.expected, r.retrieved) for r in results), 3
    )
    all_latencies = [r.latency_ms for r in results]
    out["overall"]["latency_ms_avg"] = round(statistics.mean(all_latencies), 1)
    out["overall"]["latency_ms_p95"] = round(
        statistics.quantiles(all_latencies, n=20)[-1]
        if len(all_latencies) >= 2 else all_latencies[0],
        1,
    )
    out["overall"]["tokens_avg"] = round(
        statistics.mean(r.tokens for r in results) if results else 0, 1
    )

    return out


def format_report(stats: dict) -> str:
    """Pretty-print aggregate output for console / CI log."""
    lines = ["LOCOMO-mini benchmark results"]
    lines.append("=" * 50)
    lines.append(f"Total queries: {stats['total']}")
    lines.append("")
    lines.append("By query type:")
    for qtype, s in stats["by_type"].items():
        lines.append(f"  {qtype} ({s['count']} queries):")
        for k in (5, 10):
            key = f"recall@{k}"
            if key in s:
                lines.append(f"    {key}: {s[key]:.3f}")
        lines.append(f"    mrr: {s['mrr']:.3f}")
        lines.append(
            f"    latency: avg={s['latency_ms_avg']}ms, p95={s['latency_ms_p95']}ms"
        )
    lines.append("")
    lines.append("Overall:")
    o = stats["overall"]
    lines.append(f"  recall@5:  {o.get('recall@5', '?'):.3f}")
    lines.append(f"  recall@10: {o.get('recall@10', '?'):.3f}")
    lines.append(f"  mrr:       {o.get('mrr', '?'):.3f}")
    lines.append(
        f"  latency:   avg={o.get('latency_ms_avg', '?')}ms, "
        f"p95={o.get('latency_ms_p95', '?')}ms"
    )
    lines.append(f"  tokens/q:  {o.get('tokens_avg', '?')}")
    return "\n".join(lines)


def to_dict(result: QueryResult) -> dict:
    """Serializable form of a single result."""
    return asdict(result)
