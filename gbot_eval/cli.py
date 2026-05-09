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

from gbot_eval import config as eval_config
from gbot_eval import pricing
from gbot_eval import runner as eval_runner
from gbot_eval.__version__ import __version__
from gbot_eval.pricing import list_models
from gbot_eval.reporting import compare_runs, format_matrix_table
from gbot_eval.suites import list_names

BASELINE_PATH = eval_runner.OUTPUT_ROOT.parent / "baseline.json"

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


models_app = typer.Typer(help="Pricing table inspection and overrides.")
app.add_typer(models_app, name="models")


@models_app.callback(invoke_without_command=True)
def models_root(ctx: typer.Context):
    """List models with known pricing (default action)."""
    if ctx.invoked_subcommand is not None:
        return
    table = Table(title="Known model pricing ($/1M tokens)")
    table.add_column("Model", style="cyan")
    table.add_column("Prompt", justify="right", style="green")
    table.add_column("Completion", justify="right", style="magenta")
    for model, prompt, completion in list_models():
        table.add_row(model, f"{prompt:.3f}", f"{completion:.3f}")
    console.print(table)
    console.print(
        "\n[dim]Add custom pricing via 'gbot-eval models add <id> "
        "--prompt=X --completion=Y' (writes to gbot_eval/output/pricing_overrides.json).[/dim]"
    )


@models_app.command("add")
def models_add_cmd(
    model: str = typer.Argument(..., help="Model id, e.g. 'openrouter/foo/bar-2'."),
    prompt: float = typer.Option(..., "--prompt", help="$/1M prompt tokens."),
    completion: float = typer.Option(..., "--completion", help="$/1M completion tokens."),
):
    """Add or update a model's pricing entry (persists to overrides file)."""
    pricing.add_model(model, prompt, completion)
    console.print(
        f"[green]Saved pricing for {model}[/green]: "
        f"prompt=${prompt}/1M, completion=${completion}/1M"
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


# ── baseline ────────────────────────────────────────────────────


baseline_app = typer.Typer(help="Manage and diff against the blessed baseline run.")
app.add_typer(baseline_app, name="baseline")


def _read_baseline() -> str | None:
    if not BASELINE_PATH.exists():
        return None
    try:
        data = __import__("json").loads(BASELINE_PATH.read_text())
        return data.get("run_dir")
    except Exception:
        return None


def _write_baseline(run_dir: str) -> None:
    import json as _json

    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(
        _json.dumps({"run_dir": run_dir}, indent=2)
    )


@baseline_app.callback(invoke_without_command=True)
def baseline_root(ctx: typer.Context):
    """Show the current baseline (default action)."""
    if ctx.invoked_subcommand is not None:
        return
    rd = _read_baseline()
    if not rd:
        console.print(
            "[yellow]No baseline set.[/yellow] "
            "Use 'gbot-eval baseline set --run=<timestamp>' to promote one."
        )
        return
    p = eval_runner.OUTPUT_ROOT / rd
    if not p.exists():
        console.print(f"[red]Baseline run missing on disk:[/red] {rd}")
        return
    matrix = eval_runner.load_run(p)
    console.print(f"[cyan]Baseline:[/cyan] {rd}")
    console.print(format_matrix_table(matrix))


@baseline_app.command("set")
def baseline_set_cmd(
    run: str = typer.Option(..., "--run", help="Run directory name to promote."),
):
    """Promote a run as the baseline (subsequent 'baseline diff' compares to it)."""
    p = eval_runner.OUTPUT_ROOT / run
    if not p.exists():
        console.print(f"[red]Run not found:[/red] {run}")
        raise typer.Exit(1)
    if not (p / "matrix.json").exists():
        console.print(f"[red]Run is missing matrix.json:[/red] {run}")
        raise typer.Exit(1)
    _write_baseline(run)
    console.print(f"[green]Baseline set to:[/green] {run}")


@baseline_app.command("diff")
def baseline_diff_cmd(
    run: str | None = typer.Option(
        None, "--run", help="Run to compare against baseline. Default: most recent."
    ),
):
    """Side-by-side comparison of a run vs the baseline."""
    rd = _read_baseline()
    if not rd:
        console.print(
            "[yellow]No baseline set.[/yellow] "
            "Use 'gbot-eval baseline set --run=<timestamp>' first."
        )
        raise typer.Exit(1)
    base = eval_runner.OUTPUT_ROOT / rd
    if not base.exists():
        console.print(f"[red]Baseline run missing on disk:[/red] {rd}")
        raise typer.Exit(1)

    if run:
        target = eval_runner.OUTPUT_ROOT / run
        if not target.exists():
            console.print(f"[red]Run not found:[/red] {run}")
            raise typer.Exit(1)
    else:
        runs = eval_runner.list_runs()
        if not runs:
            console.print("[yellow]No runs to diff.[/yellow]")
            raise typer.Exit(1)
        target = runs[0]

    matrix_a = eval_runner.load_run(base)
    matrix_b = eval_runner.load_run(target)
    console.print(f"[dim]baseline: {base.name}[/dim]")
    console.print(f"[dim]    run:  {target.name}[/dim]")
    console.print(compare_runs(matrix_a, matrix_b))



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
