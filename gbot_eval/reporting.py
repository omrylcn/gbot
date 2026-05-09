"""Output formatting + aggregation.

Two products:

* ``aggregate(...)`` — turns a list of ``SuiteResult`` into a single
  ``matrix.json``-shaped dict (cross-suite totals).
* ``format_matrix_table(matrix)`` — Rich table for console.
* ``compare_runs(a, b)`` — side-by-side comparison table.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.table import Table

from gbot_eval.suites.base import SuiteResult


def aggregate(suite_results: list[SuiteResult]) -> dict[str, Any]:
    """Cross-suite totals. Becomes ``matrix.json``."""
    suites: dict[str, dict[str, Any]] = {}
    total_cost = 0.0
    total_tokens = 0
    total_runtime_ms = 0
    n_cases = 0

    for sr in suite_results:
        agg = sr.aggregate
        suites[sr.name] = agg
        total_cost += float(agg.get("cost_total_usd", 0.0) or 0.0)
        total_tokens += int(agg.get("tokens_total", 0) or 0)
        total_runtime_ms += int(agg.get("runtime_ms", 0) or 0)
        n_cases += int(agg.get("cases_total", 0) or 0)

    return {
        "model": suite_results[0].model if suite_results else None,
        "ran_at": suite_results[0].ran_at if suite_results else None,
        "suites": suites,
        "totals": {
            "cost_usd": round(total_cost, 6),
            "tokens": total_tokens,
            "runtime_ms": total_runtime_ms,
            "cases": n_cases,
        },
    }


def format_matrix_table(matrix: dict[str, Any]) -> Table:
    """Rich Table for ``gbot-eval matrix``."""
    table = Table(title=f"gbot-eval — {matrix.get('model', '?')}")
    table.add_column("Suite", style="cyan", no_wrap=True)
    table.add_column("Quality", justify="right", style="green")
    table.add_column("Tokens (in/out avg)", justify="right")
    table.add_column("p95 ms", justify="right", style="yellow")
    table.add_column("Cost ($)", justify="right", style="magenta")

    for name, agg in matrix.get("suites", {}).items():
        quality = agg.get("quality")
        quality_str = f"{quality:.2f}" if quality is not None else "—"
        tokens = (
            f"{int(agg.get('tokens_in_avg', 0))}"
            f"/{int(agg.get('tokens_out_avg', 0))}"
        )
        p95 = f"{int(agg.get('latency_ms_p95', 0))}"
        cost = f"{agg.get('cost_total_usd', 0.0):.4f}"
        table.add_row(name, quality_str, tokens, p95, cost)

    totals = matrix.get("totals", {})
    table.add_section()
    table.add_row(
        "[bold]TOTALS[/bold]",
        "",
        f"{int(totals.get('tokens', 0)):,}",
        f"{int(totals.get('runtime_ms', 0)):,}",
        f"{totals.get('cost_usd', 0.0):.4f}",
    )
    return table


def compare_runs(matrix_a: dict[str, Any], matrix_b: dict[str, Any]) -> Table:
    """Side-by-side comparison."""
    model_a = matrix_a.get("model", "A")
    model_b = matrix_b.get("model", "B")
    table = Table(title=f"Comparison — {model_a}  vs  {model_b}")
    table.add_column("Suite", style="cyan", no_wrap=True)
    table.add_column(f"{model_a} quality", justify="right")
    table.add_column(f"{model_b} quality", justify="right")
    table.add_column("Δ", justify="right")
    table.add_column(f"{model_a} cost", justify="right")
    table.add_column(f"{model_b} cost", justify="right")
    table.add_column(f"{model_a} p95 ms", justify="right")
    table.add_column(f"{model_b} p95 ms", justify="right")

    suites = sorted(
        set(matrix_a.get("suites", {}).keys())
        | set(matrix_b.get("suites", {}).keys())
    )
    for name in suites:
        a = matrix_a.get("suites", {}).get(name, {})
        b = matrix_b.get("suites", {}).get(name, {})
        qa = a.get("quality")
        qb = b.get("quality")
        delta = (qb - qa) if qa is not None and qb is not None else None
        delta_color = (
            "green" if delta and delta > 0 else "red" if delta and delta < 0 else ""
        )
        delta_str = f"[{delta_color}]{delta:+.2f}[/{delta_color}]" if delta is not None else "—"
        table.add_row(
            name,
            f"{qa:.2f}" if qa is not None else "—",
            f"{qb:.2f}" if qb is not None else "—",
            delta_str,
            f"{a.get('cost_total_usd', 0.0):.4f}",
            f"{b.get('cost_total_usd', 0.0):.4f}",
            f"{int(a.get('latency_ms_p95', 0))}",
            f"{int(b.get('latency_ms_p95', 0))}",
        )

    totals_a = matrix_a.get("totals", {})
    totals_b = matrix_b.get("totals", {})
    table.add_section()
    table.add_row(
        "[bold]TOTAL COST[/bold]",
        "",
        "",
        "",
        f"{totals_a.get('cost_usd', 0.0):.4f}",
        f"{totals_b.get('cost_usd', 0.0):.4f}",
        "",
        "",
    )
    return table


def print_table(table: Table, console: Console | None = None) -> None:
    (console or Console()).print(table)
