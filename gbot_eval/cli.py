"""``gbot-eval`` Typer CLI entry point.

Subcommands:

* ``run`` — execute one or more suites against a model
* ``models`` — list known pricing entries
* ``list-runs`` — show available run directories
* ``matrix`` — print the matrix table for a run
* ``compare`` — side-by-side comparison of two runs
* ``clean`` — prune old runs

Step 5B fills in the real implementations; ``run`` becomes useful once
suites are registered (Step 5C onwards).
"""

from __future__ import annotations

import asyncio
import shutil

import typer
from rich.console import Console
from rich.table import Table

from gbot_eval import bench_providers as bench_providers_module
from gbot_eval import config as eval_config
from gbot_eval import runner as eval_runner
from gbot_eval.__version__ import __version__
from gbot_eval.pricing import list_models
from gbot_eval.reporting import (
    compare_runs,
    format_matrix_table,
    format_provider_bench,
    format_provider_winners,
)
from gbot_eval.suites import list_names

app = typer.Typer(
    help="GBot LLM evaluation CLI — measures memory + agent + stress LLM quality.",
    no_args_is_help=True,
)
console = Console()


def _version_callback(value: bool):
    if value:
        console.print(f"gbot-eval {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Print version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
):
    pass


# ── run ─────────────────────────────────────────────────────────


@app.command("run")
def run_cmd(
    model: str | None = typer.Option(
        None,
        "--model",
        help="Model under test. Default: config.memory.model or config.assistant.model.",
    ),
    suite: str | None = typer.Option(
        None,
        "--suite",
        help=(
            "Filter spec. Single suite ('memory.extraction'), group "
            "('memory'), or comma-list ('memory,agent.delegation'). "
            "Default: all registered suites."
        ),
    ),
    sample: int = typer.Option(
        100,
        "--sample",
        help=(
            "Run only the first N%% of each fixture (1-100). "
            "Useful for fast smoke tests / cost saving. The actual "
            "value is recorded in manifest.json so comparisons stay "
            "honest."
        ),
        min=1,
        max=100,
    ),
):
    """Run one or more eval suites and write results under output/runs/."""
    if not eval_config.has_api_key():
        console.print(
            "[red]No LLM provider API key found.[/red] "
            "Set OPENROUTER_API_KEY (or ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY)."
        )
        raise typer.Exit(1)

    if not list_names():
        console.print(
            "[yellow]No suites registered yet.[/yellow] "
            "Step 5C+ will add memory/agent/stress suites."
        )
        raise typer.Exit(2)

    eval_config.init_provider()
    resolved = eval_config.resolve_model(model)
    sample_label = f"{sample}%" if sample < 100 else "ALL"
    console.print(
        f"[cyan]gbot-eval run[/cyan] — model=[bold]{resolved}[/bold] "
        f"suite={suite or 'ALL'} sample={sample_label}"
    )

    results = asyncio.run(eval_runner.run_all(resolved, suite, sample_pct=sample))
    run_dir = eval_runner.write_run(results)

    console.print(f"\n[green]Run complete:[/green] {run_dir}")
    matrix = eval_runner.load_run(run_dir)
    console.print(format_matrix_table(matrix))


# ── models ──────────────────────────────────────────────────────


@app.command("models")
def models_cmd():
    """List models with known pricing."""
    table = Table(title="Known model pricing ($/1M tokens)")
    table.add_column("Model", style="cyan")
    table.add_column("Prompt", justify="right", style="green")
    table.add_column("Completion", justify="right", style="magenta")
    for model, prompt, completion in list_models():
        table.add_row(model, f"{prompt:.3f}", f"{completion:.3f}")
    console.print(table)
    console.print(
        "\n[dim]Add new entries via 'gbot-eval models add' (Step 5I).[/dim]"
    )


# ── list-runs ───────────────────────────────────────────────────


@app.command("list-runs")
def list_runs_cmd():
    """Show available run directories under gbot_eval/output/runs/."""
    runs = eval_runner.list_runs()
    if not runs:
        console.print("[yellow]No runs found.[/yellow]")
        return
    table = Table(title="Eval runs")
    table.add_column("Timestamp / model", style="cyan")
    table.add_column("Cost ($)", justify="right", style="magenta")
    table.add_column("Tokens", justify="right")
    table.add_column("Suites", justify="right")
    for run in runs:
        try:
            matrix = eval_runner.load_run(run)
            totals = matrix.get("totals", {})
            table.add_row(
                run.name,
                f"{totals.get('cost_usd', 0.0):.4f}",
                f"{int(totals.get('tokens', 0)):,}",
                f"{len(matrix.get('suites', {}))}",
            )
        except Exception as e:
            table.add_row(run.name, "[red]error[/red]", "—", str(e)[:40])
    console.print(table)


# ── matrix ──────────────────────────────────────────────────────


@app.command("matrix")
def matrix_cmd(
    run: str | None = typer.Option(
        None,
        "--run",
        help="Run directory name (e.g. '2026-05-09T12-00-00_...'). Default: most recent.",
    ),
):
    """Print the matrix table for a run."""
    runs = eval_runner.list_runs()
    if not runs:
        console.print("[yellow]No runs found.[/yellow]")
        raise typer.Exit(1)

    if run:
        target = eval_runner.OUTPUT_ROOT / run
        if not target.exists():
            console.print(f"[red]Run not found:[/red] {run}")
            raise typer.Exit(1)
    else:
        target = runs[0]

    matrix = eval_runner.load_run(target)
    console.print(f"[dim]{target.name}[/dim]")
    console.print(format_matrix_table(matrix))


# ── compare ─────────────────────────────────────────────────────


@app.command("compare")
def compare_cmd(
    run_a: str = typer.Argument(..., help="First run directory name."),
    run_b: str = typer.Argument(..., help="Second run directory name."),
):
    """Side-by-side comparison of two runs."""
    a_path = eval_runner.OUTPUT_ROOT / run_a
    b_path = eval_runner.OUTPUT_ROOT / run_b
    for p in (a_path, b_path):
        if not p.exists():
            console.print(f"[red]Run not found:[/red] {p.name}")
            raise typer.Exit(1)
    matrix_a = eval_runner.load_run(a_path)
    matrix_b = eval_runner.load_run(b_path)
    console.print(compare_runs(matrix_a, matrix_b))


# ── clean ───────────────────────────────────────────────────────


@app.command("clean")
def clean_cmd(
    keep: int = typer.Option(
        20, "--keep", help="Keep the N most recent runs; delete the rest."
    ),
):
    """Prune old run directories."""
    runs = eval_runner.list_runs()
    if len(runs) <= keep:
        console.print(f"[green]{len(runs)} runs ≤ keep={keep} — nothing to do.[/green]")
        return
    to_remove = runs[keep:]
    for r in to_remove:
        shutil.rmtree(r)
    console.print(f"[yellow]Removed {len(to_remove)} run(s); {keep} kept.[/yellow]")


# ── bench-providers ────────────────────────────────────────────


@app.command("bench-providers")
def bench_providers_cmd(
    models: str = typer.Option(
        "openrouter/google/gemini-3-flash-preview,"
        "openrouter/moonshotai/kimi-k2.6,"
        "openrouter/minimax/minimax-m2.7,"
        "openrouter/openai/gpt-4o-mini",
        "--models",
        help="Comma-separated model ids to benchmark.",
    ),
):
    """Head-to-head LiteLLM vs OpenRouter SDK bench (4 areas × 3 cases)."""
    if not eval_config.has_api_key():
        console.print(
            "[red]No LLM provider API key found.[/red] "
            "Set OPENROUTER_API_KEY."
        )
        raise typer.Exit(1)

    model_list = [m.strip() for m in models.split(",") if m.strip()]
    console.print(
        f"[cyan]bench-providers[/cyan] — {len(model_list)} model × 2 provider × 12 case"
    )
    matrix = asyncio.run(bench_providers_module.run_bench(model_list))
    run_dir = bench_providers_module.write_bench_run(matrix)
    console.print(f"\n[green]Run complete:[/green] {run_dir}")
    console.print(format_provider_bench(matrix))
    console.print()
    console.print(format_provider_winners(matrix))
    totals = matrix["totals"]
    console.print(
        f"\n[dim]Totals: {totals['calls']} calls, "
        f"{totals['tokens']:,} tokens, "
        f"${totals['cost_usd']:.4f}, "
        f"failures={totals['failures']}[/dim]"
    )


# ── list ────────────────────────────────────────────────────────


@app.command("list")
def list_cmd():
    """List registered suites."""
    names = list_names()
    if not names:
        console.print("[yellow]No suites registered yet.[/yellow]")
        return
    for n in names:
        console.print(f"  • {n}")


if __name__ == "__main__":
    app()
