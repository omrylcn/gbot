"""GraphBot CLI — Typer-based command-line interface."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from graphbot import __version__

app = typer.Typer(
    name="graphbot",
    help="graphbot - LangGraph-based AI assistant",
    no_args_is_help=True,
)

console = Console()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"graphbot v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None, "--version", "-v", callback=_version_callback, is_eager=True
    ),
) -> None:
    """graphbot - LangGraph-based AI assistant."""


# ════════════════════════════════════════════════════════════
# run — start API server
# ════════════════════════════════════════════════════════════


@app.command()
def run(
    port: int = typer.Option(8000, "--port", "-p", help="Port number"),
    host: str = typer.Option("0.0.0.0", "--host", "-h", help="Host address"),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload"),
) -> None:
    """Start the API server (uvicorn)."""
    import uvicorn

    console.print(f"[green]Starting graphbot API on {host}:{port}[/green]")
    uvicorn.run("graphbot.api.app:app", host=host, port=port, reload=reload)


# ════════════════════════════════════════════════════════════
# chat — terminal chat
# ════════════════════════════════════════════════════════════


@app.command()
def chat(
    message: str | None = typer.Option(None, "--message", "-m", help="Single message to send"),
    session: str = typer.Option("cli:default", "--session", "-s", help="Session ID"),
) -> None:
    """Chat with the assistant from the terminal."""
    from graphbot.agent.runner import GraphRunner
    from graphbot.core.config.loader import load_config
    from graphbot.memory.store import MemoryStore

    config = load_config()
    db = MemoryStore(config.database.path)
    runner = GraphRunner(config, db)

    user_id = "cli_user"
    channel = "cli"

    db.get_or_create_user(user_id, name="CLI User")

    if message:
        # Single message mode
        response, _ = asyncio.run(runner.process(user_id, channel, message, session))
        console.print(f"\n[bold cyan]graphbot:[/bold cyan] {response}\n")
    else:
        # Interactive mode
        console.print("[bold]graphbot interactive mode[/bold] (type 'exit' or 'quit' to leave)\n")

        async def _interactive() -> None:
            sid = session
            while True:
                try:
                    user_input = console.input("[bold blue]You:[/bold blue] ")
                except (KeyboardInterrupt, EOFError):
                    console.print("\nBye!")
                    break

                text = user_input.strip()
                if not text:
                    continue
                if text.lower() in ("exit", "quit"):
                    console.print("Bye!")
                    break

                response, sid = await runner.process(user_id, channel, text, sid)
                console.print(f"\n[bold cyan]graphbot:[/bold cyan] {response}\n")

        asyncio.run(_interactive())


# ════════════════════════════════════════════════════════════
# status — config + DB info
# ════════════════════════════════════════════════════════════


@app.command()
def status() -> None:
    """Show configuration and database status."""
    from graphbot.core.config.loader import load_config
    from graphbot.memory.store import MemoryStore

    config = load_config()
    db = MemoryStore(config.database.path)

    # Count users
    with db._get_conn() as conn:
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        session_count = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE status = 'active'"
        ).fetchone()[0]

    table = Table(title="graphbot status")
    table.add_column("Key", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Version", __version__)
    table.add_row("Model", config.assistant.model)
    table.add_row("DB Path", config.database.path)
    table.add_row("Users", str(user_count))
    table.add_row("Active Sessions", str(session_count))

    console.print(table)


# ════════════════════════════════════════════════════════════
# cron — cron job management (sub-command group)
# ════════════════════════════════════════════════════════════

cron_app = typer.Typer(help="Manage cron jobs")
app.add_typer(cron_app, name="cron")


@cron_app.command("list")
def cron_list() -> None:
    """List all cron jobs."""
    from graphbot.core.config.loader import load_config
    from graphbot.memory.store import MemoryStore

    config = load_config()
    db = MemoryStore(config.database.path)

    jobs = db.get_cron_jobs()

    if not jobs:
        console.print("[dim]No cron jobs found.[/dim]")
        return

    table = Table(title="Cron Jobs")
    table.add_column("ID", style="cyan")
    table.add_column("User", style="blue")
    table.add_column("Cron", style="yellow")
    table.add_column("Message", style="white")
    table.add_column("Enabled", style="green")

    for job in jobs:
        table.add_row(
            job["job_id"],
            job["user_id"],
            job["cron_expr"],
            job["message"],
            str(bool(job["enabled"])),
        )

    console.print(table)


@cron_app.command("remove")
def cron_remove(
    job_id: str = typer.Argument(help="Cron job ID to remove"),
) -> None:
    """Remove a cron job by ID."""
    from graphbot.core.config.loader import load_config
    from graphbot.memory.store import MemoryStore

    config = load_config()
    db = MemoryStore(config.database.path)

    db.remove_cron_job(job_id)
    console.print(f"[green]Removed cron job:[/green] {job_id}")
