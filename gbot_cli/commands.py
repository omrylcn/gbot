"""gbot CLI — Typer-based command-line interface."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from graphbot import __version__

app = typer.Typer(
    name="gbot",
    help="gbot - LangGraph-based AI assistant",
    no_args_is_help=False,
    invoke_without_command=True,
)

console = Console()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"gbot v{__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        None, "--version", "-v", callback=_version_callback, is_eager=True
    ),
) -> None:
    """gbot - LangGraph-based AI assistant."""
    if ctx.invoked_subcommand is None:
        # Default: open interactive REPL (same as `gbot chat`)
        _start_repl()


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

    console.print(f"[green]Starting gbot API on {host}:{port}[/green]")
    uvicorn.run("graphbot.api.app:app", host=host, port=port, reload=reload)


# ════════════════════════════════════════════════════════════
# chat — terminal chat (API-backed REPL or local standalone)
# ════════════════════════════════════════════════════════════


def _start_repl(
    server: str = "http://localhost:8000",
    token: str | None = None,
    api_key: str | None = None,
    message: str | None = None,
    session: str | None = None,
) -> None:
    """Core REPL logic — shared by `gbot` (bare) and `gbot chat`."""
    from gbot_cli.credentials import load_credentials

    creds = load_credentials()
    resolved_server = server if server != "http://localhost:8000" else creds.get("server_url", server)
    resolved_token = token or creds.get("token")
    resolved_api_key = api_key or creds.get("api_key")
    import getpass

    user_id = creds.get("user_id", getpass.getuser())

    from gbot_cli.client import GraphBotClient

    client = GraphBotClient(
        base_url=resolved_server,
        token=resolved_token,
        api_key=resolved_api_key,
    )

    try:
        if message:
            from gbot_cli.client import APIError

            try:
                data = client.chat(message, session_id=session)
                console.print(f"\n[bold cyan]gbot:[/bold cyan] {data['response']}\n")
            except APIError as e:
                console.print(f"[red]Error:[/red] {e.detail}")
                raise typer.Exit(code=1)
        else:
            from gbot_cli.repl import REPL

            repl = REPL(client, user_id, console=console)
            if session:
                repl.session_id = session
            repl.start()
    finally:
        client.close()


@app.command()
def chat(
    server: str = typer.Option("http://localhost:8000", "--server", "-s", help="API server URL"),
    token: str | None = typer.Option(None, "--token", "-t", help="Bearer token"),
    api_key: str | None = typer.Option(None, "--api-key", "-k", help="API key"),
    message: str | None = typer.Option(None, "--message", "-m", help="Single message (non-interactive)"),
    session: str | None = typer.Option(None, "--session", help="Session ID"),
    local: bool = typer.Option(False, "--local", "-l", help="Local standalone mode (no API server)"),
) -> None:
    """Chat with the assistant — connects to the API server by default."""
    if local:
        _chat_local(message, session or "cli:default")
        return
    _start_repl(server=server, token=token, api_key=api_key, message=message, session=session)


def _chat_local(message: str | None, session: str) -> None:
    """Legacy local standalone chat (direct GraphRunner, no API)."""
    from graphbot.agent.runner import GraphRunner
    from graphbot.core.config.loader import load_config
    from graphbot.memory.store import MemoryStore

    config = load_config()
    db = MemoryStore(config.database.path)
    runner = GraphRunner(config, db)

    if config.assistant.owner is not None:
        user_id = config.assistant.owner.username
        db.get_or_create_user(user_id, name=config.assistant.owner.name or None)
    else:
        user_id = "cli_user"
        db.get_or_create_user(user_id, name="CLI User")
    channel = "cli"

    if message:
        response, _ = asyncio.run(runner.process(user_id, channel, message, session))
        console.print(f"\n[bold cyan]gbot:[/bold cyan] {response}\n")
    else:
        console.print("[bold]gbot interactive mode[/bold] (type 'exit' or 'quit' to leave)\n")

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
                console.print(f"\n[bold cyan]gbot:[/bold cyan] {response}\n")

        asyncio.run(_interactive())


# ════════════════════════════════════════════════════════════
# login / logout — credential management
# ════════════════════════════════════════════════════════════


@app.command()
def login(
    server: str = typer.Option("http://localhost:8000", "--server", "-s", help="API server URL"),
    user_id: str = typer.Argument(help="User ID"),
    password: str = typer.Option(..., "--password", "-p", prompt=True, hide_input=True, help="Password"),
) -> None:
    """Login to the API server and save credentials."""
    from gbot_cli.client import APIError, GraphBotClient
    from gbot_cli.credentials import save_credentials

    client = GraphBotClient(base_url=server)
    try:
        data = client.login(user_id, password)
        token = data.get("token")
        save_credentials({
            "server_url": server,
            "user_id": user_id,
            "token": token,
        })
        console.print(f"[green]Logged in as[/green] {user_id}")
    except APIError as e:
        console.print(f"[red]Login failed:[/red] {e.detail}")
        raise typer.Exit(code=1)
    finally:
        client.close()


@app.command()
def logout() -> None:
    """Clear saved credentials."""
    from gbot_cli.credentials import clear_credentials

    clear_credentials()
    console.print("[green]Logged out.[/green]")


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
            "SELECT COUNT(*) FROM sessions WHERE ended_at IS NULL"
        ).fetchone()[0]

    table = Table(title="gbot status")
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


# ════════════════════════════════════════════════════════════
# user — user management (sub-command group)
# ════════════════════════════════════════════════════════════

user_app = typer.Typer(help="Manage users")
app.add_typer(user_app, name="user")


@user_app.command("add")
def user_add(
    username: str = typer.Argument(help="User ID (e.g. 'ali')"),
    name: str = typer.Option("", "--name", "-n", help="Display name"),
    password: str | None = typer.Option(None, "--password", "-p", help="Password (for API auth)"),
    telegram: str | None = typer.Option(None, "--telegram", "-t", help="Telegram bot token"),
) -> None:
    """Add a new user, optionally with password and Telegram link."""
    from graphbot.core.config.loader import load_config
    from graphbot.memory.store import MemoryStore

    config = load_config()
    db = MemoryStore(config.database.path)

    if db.user_exists(username):
        console.print(f"[yellow]User already exists:[/yellow] {username}")
        return

    db.get_or_create_user(username, name=name or None)
    console.print(f"[green]User created:[/green] {username}")

    if password:
        from graphbot.api.auth import hash_password

        db.set_password(username, hash_password(password))
        console.print("  [dim]Password set[/dim]")

    if telegram:
        db.link_channel(username, "telegram", telegram)
        console.print(f"  [dim]Linked telegram:{telegram[:15]}...[/dim]")


@user_app.command("list")
def user_list() -> None:
    """List all users and their linked channels."""
    from graphbot.core.config.loader import load_config
    from graphbot.memory.store import MemoryStore

    config = load_config()
    db = MemoryStore(config.database.path)

    users = db.list_users()
    if not users:
        console.print("[dim]No users found.[/dim]")
        return

    table = Table(title="Users")
    table.add_column("User ID", style="cyan")
    table.add_column("Name", style="blue")
    table.add_column("Channels", style="yellow")
    table.add_column("Created", style="dim")

    for u in users:
        channels_str = ", ".join(
            f"{c['channel']}:{c['channel_user_id']}" for c in u["channels"]
        ) or "-"
        table.add_row(u["user_id"], u["name"] or "-", channels_str, u["created_at"])

    console.print(table)


@user_app.command("remove")
def user_remove(
    username: str = typer.Argument(help="User ID to remove"),
) -> None:
    """Remove a user and their channel links."""
    from graphbot.core.config.loader import load_config
    from graphbot.memory.store import MemoryStore

    config = load_config()
    db = MemoryStore(config.database.path)

    if db.delete_user(username):
        console.print(f"[green]Removed user:[/green] {username}")
    else:
        console.print(f"[red]User not found:[/red] {username}")


@user_app.command("set-password")
def user_set_password(
    username: str = typer.Argument(help="User ID"),
    password: str = typer.Argument(help="New password"),
) -> None:
    """Set or change password for an existing user."""
    from graphbot.api.auth import hash_password
    from graphbot.core.config.loader import load_config
    from graphbot.memory.store import MemoryStore

    config = load_config()
    db = MemoryStore(config.database.path)

    if not db.user_exists(username):
        console.print(f"[red]User not found:[/red] {username}")
        raise typer.Exit(code=1)

    db.set_password(username, hash_password(password))
    console.print(f"[green]Password updated for[/green] {username}")


@user_app.command("link")
def user_link(
    username: str = typer.Argument(help="User ID"),
    channel: str = typer.Argument(help="Channel name (telegram, discord, ...)"),
    channel_user_id: str = typer.Argument(help="User's ID on that channel"),
) -> None:
    """Link a channel identity to a user."""
    from graphbot.core.config.loader import load_config
    from graphbot.memory.store import MemoryStore

    config = load_config()
    db = MemoryStore(config.database.path)

    if not db.user_exists(username):
        console.print(f"[red]User not found:[/red] {username}")
        raise typer.Exit(code=1)

    db.link_channel(username, channel, channel_user_id)
    console.print(f"[green]Linked[/green] {channel}:{channel_user_id} [green]to[/green] {username}")
