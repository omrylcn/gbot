"""``gbot-eval`` Typer CLI entry point.

Step 5A: minimal — only ``--help`` and ``models`` placeholder. Step 5I
fills in run/matrix/compare/baseline/list-runs/clean.
"""

from __future__ import annotations

import typer
from rich.console import Console

from gbot_eval.__version__ import __version__

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


@app.command("models")
def models_cmd():
    """List models with known pricing (placeholder — Step 5B fills in)."""
    console.print(
        "[yellow]Models pricing table is not implemented yet (Step 5B).[/yellow]"
    )


if __name__ == "__main__":
    app()
