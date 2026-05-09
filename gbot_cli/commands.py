"""gbot CLI — Typer-based command-line interface."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from gbot import __version__

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
    uvicorn.run("gbot.api.app:app", host=host, port=port, reload=reload)


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
    from gbot.agent.runner import GraphRunner
    from gbot.core.config.loader import load_config
    from gbot.memory.store import MemoryStore

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
def status(
    channel: str | None = typer.Option(None, "--channel", "-c", help="Filter active session by channel (api, telegram, ...)"),
    user: str | None = typer.Option(None, "--user", "-u", help="User ID for session info (default: owner)"),
) -> None:
    """Show comprehensive system stats: context, tools, sessions, tokens."""
    from rich.panel import Panel
    from rich.text import Text

    from gbot.agent.context import ContextBuilder
    from gbot.agent.tools import make_tools
    from gbot.core.config.loader import load_config
    from gbot.memory.store import MemoryStore

    config = load_config()
    db = MemoryStore(config.database.path)
    target_user = user or config.owner_user_id

    # ── System info ──
    sys_table = Table(show_header=False, box=None, padding=(0, 2))
    sys_table.add_column(style="dim")
    sys_table.add_column(style="bold")
    sys_table.add_row("Version", __version__)
    sys_table.add_row("Model", config.assistant.model)
    sys_table.add_row("Thinking", "on" if config.assistant.thinking else "off")
    sys_table.add_row("Token Limit", f"{config.assistant.session_token_limit:,}")
    console.print(Panel(sys_table, title="System", border_style="blue"))

    # ── Context layers ──
    ctx = ContextBuilder(config, db)
    stats = ctx.get_context_stats(target_user)

    ctx_table = Table(box=None, padding=(0, 2))
    ctx_table.add_column("Layer", style="cyan")
    ctx_table.add_column("Tokens", justify="right", style="green")
    ctx_table.add_column("Chars", justify="right", style="dim")
    ctx_table.add_column("Budget", justify="right", style="yellow")
    ctx_table.add_column("", style="dim")

    for layer in stats["layers"]:
        bar_len = min(layer["tokens"] // 20, 30)  # scale: 20 tokens = 1 char
        bar = Text("█" * bar_len, style="green" if not layer["truncated"] else "red")
        budget_str = str(layer["budget"]) if layer["budget"] else "-"
        ctx_table.add_row(
            layer["layer"],
            f"{layer['tokens']:,}",
            f"{layer['chars']:,}",
            budget_str,
            bar,
        )

    ctx_table.add_section()
    ctx_table.add_row(
        "TOTAL",
        f"{stats['total_tokens']:,}",
        f"{stats['total_chars']:,}",
        "",
        "",
    )
    console.print(Panel(ctx_table, title="Context Layers", border_style="green"))

    # ── Tools ──
    registry = make_tools(config, db)
    tool_table = Table(box=None, padding=(0, 2))
    tool_table.add_column("Group", style="cyan")
    tool_table.add_column("Tools", justify="right", style="green")
    tool_table.add_column("Names", style="dim")

    groups = registry.get_groups_summary()
    for group, names in sorted(groups.items()):
        tool_table.add_row(group, str(len(names)), ", ".join(sorted(names)))

    tool_table.add_section()
    tool_table.add_row(
        "TOTAL",
        str(len(registry.get_all_tools())),
        f"({len(registry)} registered)",
    )
    console.print(Panel(tool_table, title="Tools", border_style="yellow"))

    # ── Data ──
    with db._get_conn() as conn:
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active_sessions = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE ended_at IS NULL"
        ).fetchone()[0]
        total_sessions = conn.execute(
            "SELECT COUNT(*) FROM sessions"
        ).fetchone()[0]
        total_tokens = conn.execute(
            "SELECT COALESCE(SUM(token_count), 0) FROM sessions"
        ).fetchone()[0]
        total_messages = conn.execute(
            "SELECT COUNT(*) FROM messages"
        ).fetchone()[0]
        cron_count = conn.execute(
            "SELECT COUNT(*) FROM background_tasks WHERE execution_type IN ('recurring', 'monitor') AND enabled = 1"
        ).fetchone()[0]
        reminder_count = conn.execute(
            "SELECT COUNT(*) FROM background_tasks WHERE execution_type = 'delayed' AND status = 'pending'"
        ).fetchone()[0]
        note_count = conn.execute(
            "SELECT COUNT(*) FROM user_notes"
        ).fetchone()[0]

    data_table = Table(show_header=False, box=None, padding=(0, 2))
    data_table.add_column(style="dim")
    data_table.add_column(justify="right", style="bold")

    data_table.add_row("Users", str(user_count))
    data_table.add_row("Sessions", f"{active_sessions} active / {total_sessions} total")
    data_table.add_row("Total Tokens", f"{total_tokens:,}")
    data_table.add_row("Messages", f"{total_messages:,}")
    data_table.add_row("Notes", str(note_count))
    data_table.add_row("Cron Jobs", str(cron_count))
    data_table.add_row("Reminders", str(reminder_count))
    console.print(Panel(data_table, title="Data", border_style="magenta"))

    # ── Active Session (optionally filtered by user and/or channel) ──
    if channel:
        with db._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE user_id = ? AND channel = ? "
                "AND ended_at IS NULL ORDER BY started_at DESC LIMIT 1",
                (target_user, channel),
            ).fetchone()
            active_session = dict(row) if row else None
    else:
        active_session = db.get_active_session(target_user)
    session_title = f"Active Session ({target_user})" if user else "Active Session"
    if active_session:
        sid = active_session["session_id"]
        msgs = db.get_session_messages(sid)
        user_msgs = sum(1 for m in msgs if m["role"] == "user")
        asst_msgs = sum(1 for m in msgs if m["role"] == "assistant")
        tool_msgs = sum(1 for m in msgs if m["role"] == "tool")
        tok = active_session.get("token_count", 0)
        tok_limit = config.assistant.session_token_limit
        tok_pct = tok / tok_limit * 100 if tok_limit else 0

        bar_width = 30
        filled = int(bar_width * tok_pct / 100)
        bar_color = "green" if tok_pct < 60 else ("yellow" if tok_pct < 85 else "red")
        bar = Text(
            "█" * filled + "░" * (bar_width - filled),
            style=bar_color,
        )

        sess_table = Table(show_header=False, box=None, padding=(0, 2))
        sess_table.add_column(style="dim")
        sess_table.add_column(style="bold")

        sess_table.add_row("Session", sid[:12] + "...")
        sess_table.add_row("Channel", active_session.get("channel", "?"))
        sess_table.add_row(
            "Messages",
            f"{len(msgs)} ({user_msgs} user, {asst_msgs} assistant, {tool_msgs} tool)",
        )
        sess_table.add_row("Tokens", f"{tok:,} / {tok_limit:,} ({tok_pct:.0f}%)")
        sess_table.add_row("Progress", bar)
        sess_table.add_row("Started", active_session.get("started_at", "?"))
        console.print(Panel(sess_table, title=session_title, border_style="cyan"))


# ════════════════════════════════════════════════════════════
# cron — cron job management (sub-command group)
# ════════════════════════════════════════════════════════════

cron_app = typer.Typer(help="Manage cron jobs")
app.add_typer(cron_app, name="cron")


@cron_app.command("list")
def cron_list() -> None:
    """List all cron jobs."""
    from gbot.core.config.loader import load_config
    from gbot.memory.store import MemoryStore

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
    from gbot.core.config.loader import load_config
    from gbot.memory.store import MemoryStore

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
    from gbot.core.config.loader import load_config
    from gbot.memory.store import MemoryStore

    config = load_config()
    db = MemoryStore(config.database.path)

    if db.user_exists(username):
        console.print(f"[yellow]User already exists:[/yellow] {username}")
        return

    db.get_or_create_user(username, name=name or None)
    console.print(f"[green]User created:[/green] {username}")

    if password:
        from gbot.api.auth import hash_password

        db.set_password(username, hash_password(password))
        console.print("  [dim]Password set[/dim]")

    if telegram:
        db.link_channel(username, "telegram", telegram)
        console.print(f"  [dim]Linked telegram:{telegram[:15]}...[/dim]")


@user_app.command("list")
def user_list() -> None:
    """List all users and their linked channels."""
    from gbot.core.config.loader import load_config
    from gbot.memory.store import MemoryStore

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
    from gbot.core.config.loader import load_config
    from gbot.memory.store import MemoryStore

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
    from gbot.api.auth import hash_password
    from gbot.core.config.loader import load_config
    from gbot.memory.store import MemoryStore

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
    from gbot.core.config.loader import load_config
    from gbot.memory.store import MemoryStore

    config = load_config()
    db = MemoryStore(config.database.path)

    if not db.user_exists(username):
        console.print(f"[red]User not found:[/red] {username}")
        raise typer.Exit(code=1)

    db.link_channel(username, channel, channel_user_id)
    console.print(f"[green]Linked[/green] {channel}:{channel_user_id} [green]to[/green] {username}")


# ════════════════════════════════════════════════════════════
# memory — direct DB access for inspection + lifecycle ops
# (Faz 22H — quick path that bypasses the admin API token wall)
# ════════════════════════════════════════════════════════════

memory_app = typer.Typer(help="Inspect and manage user memory.")
app.add_typer(memory_app, name="memory")


def _open_db():
    from gbot.core.config.loader import load_config
    from gbot.memory.store import MemoryStore

    config = load_config()
    return MemoryStore(config.database.path)


@memory_app.command("stats")
def memory_stats(
    user_id: str = typer.Argument(default="owner", help="User to inspect."),
) -> None:
    """Compact memory snapshot — counts by type and lifecycle state."""
    db = _open_db()
    with db._get_conn() as conn:
        rows = conn.execute(
            """SELECT fact_type, state, COUNT(*) AS n
               FROM memory_facts WHERE user_id = ?
               GROUP BY fact_type, state ORDER BY fact_type, state""",
            (user_id,),
        ).fetchall()
        rel_count = conn.execute(
            "SELECT COUNT(*) FROM memory_relations WHERE user_id = ? AND valid_until IS NULL",
            (user_id,),
        ).fetchone()[0]
        page_count = conn.execute(
            "SELECT COUNT(*) FROM memory_entity_pages WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]

    if not rows:
        console.print(f"[dim]No facts found for {user_id}.[/dim]")
        return
    table = Table(title=f"Memory snapshot — {user_id}")
    table.add_column("fact_type", style="cyan")
    table.add_column("state", style="yellow")
    table.add_column("count", justify="right", style="green")
    for r in rows:
        table.add_row(r["fact_type"], r["state"] or "—", str(r["n"]))
    console.print(table)
    console.print(
        f"\n[dim]Live relations: {rel_count}   Entity pages: {page_count}[/dim]"
    )


@memory_app.command("facts")
def memory_facts(
    user_id: str = typer.Argument(default="owner"),
    state: str = typer.Option(
        None, "--state",
        help="Filter by lifecycle state: active|weak|inhibited|archived",
    ),
    fact_type: str = typer.Option(None, "--type", help="Filter by fact_type"),
    limit: int = typer.Option(50, "--limit"),
    contains: str = typer.Option(None, "--contains", help="Substring match on content"),
) -> None:
    """List facts with state, type, importance, age."""
    db = _open_db()
    sql = "SELECT fact_id, fact_type, state, importance, access_count, created_at, content FROM memory_facts WHERE user_id = ?"
    params: list = [user_id]
    if state:
        sql += " AND state = ?"
        params.append(state)
    if fact_type:
        sql += " AND fact_type = ?"
        params.append(fact_type)
    if contains:
        sql += " AND content LIKE ?"
        params.append(f"%{contains}%")
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with db._get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    if not rows:
        console.print("[dim]No facts matched.[/dim]")
        return
    table = Table(title=f"{len(rows)} fact(s)")
    table.add_column("fact_id", style="dim")
    table.add_column("type", style="cyan")
    table.add_column("state", style="yellow")
    table.add_column("imp", justify="right")
    table.add_column("acc", justify="right")
    table.add_column("created", style="dim")
    table.add_column("content", style="white")
    for r in rows:
        table.add_row(
            r["fact_id"][:8],
            r["fact_type"] or "—",
            r["state"] or "—",
            f"{r['importance']:.2f}",
            str(r["access_count"]),
            (r["created_at"] or "")[:16],
            (r["content"] or "")[:80],
        )
    console.print(table)


@memory_app.command("inhibit")
def memory_inhibit(
    fact_id: str = typer.Argument(help="Fact ID (full or 8-char prefix)"),
    days: int = typer.Option(7, "--days", help="Hold duration"),
) -> None:
    """Temporarily exclude a fact from retrieval (Faz 22G)."""
    db = _open_db()
    full_id = _resolve_fact_id(db, fact_id)
    if not full_id:
        console.print(f"[red]Fact not found:[/red] {fact_id}")
        raise typer.Exit(1)
    ok = db.inhibit_fact(full_id, hold_days=days)
    if ok:
        console.print(f"[yellow]Inhibited[/yellow] {full_id[:8]} for {days} days")
    else:
        console.print(
            "[dim]No change — fact already archived or not active.[/dim]"
        )


@memory_app.command("restore")
def memory_restore(
    fact_id: str = typer.Argument(help="Fact ID (full or 8-char prefix)"),
) -> None:
    """Restore an INHIBITED fact to ACTIVE (Faz 22G)."""
    db = _open_db()
    full_id = _resolve_fact_id(db, fact_id)
    if not full_id:
        console.print(f"[red]Fact not found:[/red] {fact_id}")
        raise typer.Exit(1)
    ok = db.restore_fact(full_id)
    if ok:
        console.print(f"[green]Restored[/green] {full_id[:8]} to active")
    else:
        console.print("[dim]Fact wasn't inhibited; nothing to restore.[/dim]")


@memory_app.command("decay")
def memory_decay(
    user_id: str = typer.Argument(default="owner"),
    archive_threshold: float = typer.Option(0.1, "--threshold"),
) -> None:
    """Run apply_decay manually — fade ACTIVE→WEAK, archive low-importance."""
    db = _open_db()
    result = db.apply_decay(user_id, archive_threshold=archive_threshold)
    table = Table(title=f"Decay run — {user_id}")
    table.add_column("metric", style="cyan")
    table.add_column("count", justify="right", style="green")
    table.add_row("faded (active→weak / weak→deeper)", str(result["faded"]))
    table.add_row("archived", str(result["archived"]))
    table.add_row("auto-restored from inhibited", str(result.get("restored", 0)))
    console.print(table)
    by_type = result.get("by_type", {})
    if by_type:
        console.print("\n[dim]Faded by type:[/dim]")
        for t, n in by_type.items():
            console.print(f"  {t}: {n}")


@memory_app.command("archive-old")
def memory_archive_old(
    user_id: str = typer.Argument(default="owner"),
    days: int = typer.Option(14, "--days", help="Min age in days"),
    fact_type: str = typer.Option(
        "episodic", "--type",
        help="Restrict to a fact_type (default 'episodic'); use 'all' to skip the filter",
    ),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Archive facts older than ``days`` regardless of access_count.

    ``apply_decay`` skips frequently-accessed facts on purpose so loved
    rows don't fade. That's right for semantic / preference, wrong for
    stale episodic ones (a 'meeting yesterday' note is useless 23 days
    later even if it kept getting retrieved). This command bypasses the
    access_count guard for the cases where age alone should win.
    """
    db = _open_db()
    sql = (
        "SELECT fact_id, fact_type, content, importance, access_count, created_at "
        "FROM memory_facts WHERE user_id = ? "
        "  AND valid_until IS NULL "
        "  AND julianday('now') - julianday(created_at) > ?"
    )
    params: list = [user_id, days]
    if fact_type and fact_type != "all":
        sql += " AND fact_type = ?"
        params.append(fact_type)
    sql += " ORDER BY created_at"
    with db._get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    if not rows:
        console.print(
            f"[dim]No {fact_type} facts older than {days}d for {user_id}.[/dim]"
        )
        return

    console.print(
        f"[yellow]Found {len(rows)} {fact_type} fact(s) older than {days}d:[/yellow]"
    )
    for r in rows[:10]:
        console.print(
            f"  {(r['fact_id'] or '')[:8]}  "
            f"[dim]{(r['created_at'] or '')[:10]}[/dim]  "
            f"acc={r['access_count']}  "
            f"{(r['content'] or '')[:80]}"
        )
    if len(rows) > 10:
        console.print(f"  [dim]... +{len(rows) - 10} more[/dim]")

    if dry_run:
        console.print("\n[cyan]Dry run — nothing changed.[/cyan]")
        return

    for r in rows:
        db.invalidate_fact(r["fact_id"])
    console.print(f"\n[green]Archived {len(rows)} fact(s).[/green]")


@memory_app.command("forget")
def memory_forget(
    entity: str = typer.Argument(help="Canonical entity name to archive"),
    user_id: str = typer.Argument(default="owner"),
) -> None:
    """Cascade-archive an entity — invalidates relations + facts mentioning it,
    deletes its derived entity page.
    """
    db = _open_db()
    surface_forms = {entity}
    try:
        from gbot.memory.entities import EntityResolver

        resolver = EntityResolver(db)
        surface_forms |= resolver.expand(user_id, entity)
    except Exception:
        pass

    with db._get_conn() as conn:
        # Relations
        conn.execute(
            """UPDATE memory_relations SET valid_until = CURRENT_TIMESTAMP
               WHERE user_id = ? AND valid_until IS NULL
                 AND (canonical_source = ? OR canonical_target = ?
                      OR source_entity = ? OR target_entity = ?)""",
            (user_id, entity, entity, entity, entity),
        )
        rel_n = conn.execute("SELECT changes()").fetchone()[0]
        # Facts
        forms_lower = [s.lower() for s in surface_forms if s]
        rows = conn.execute(
            "SELECT fact_id, content FROM memory_facts WHERE user_id = ? AND valid_until IS NULL",
            (user_id,),
        ).fetchall()
        fact_n = 0
        for r in rows:
            content_lc = (r["content"] or "").lower()
            if any(form in content_lc for form in forms_lower):
                db.invalidate_fact(r["fact_id"])
                fact_n += 1
        conn.execute(
            "DELETE FROM memory_entity_pages WHERE user_id = ? AND entity_canonical = ?",
            (user_id, entity),
        )
        page_n = conn.execute("SELECT changes()").fetchone()[0]
        conn.commit()

    console.print(
        f"[yellow]Forgot[/yellow] {entity}: "
        f"{rel_n} relation(s) invalidated, "
        f"{fact_n} fact(s) archived, "
        f"{page_n} entity page(s) deleted."
    )


def _resolve_fact_id(db, prefix: str) -> str | None:
    """Resolve an 8-char prefix to a full fact_id; returns full id unchanged."""
    if not prefix:
        return None
    with db._get_conn() as conn:
        if len(prefix) >= 24:
            row = conn.execute(
                "SELECT fact_id FROM memory_facts WHERE fact_id = ?",
                (prefix,),
            ).fetchone()
            return row[0] if row else None
        rows = conn.execute(
            "SELECT fact_id FROM memory_facts WHERE fact_id LIKE ? LIMIT 2",
            (prefix + "%",),
        ).fetchall()
    if len(rows) == 1:
        return rows[0][0]
    if len(rows) > 1:
        console.print(f"[red]Ambiguous prefix:[/red] {prefix} matches multiple")
    return None
