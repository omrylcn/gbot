"""Rich output formatters for the CLI."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table


def render_sessions_table(console: Console, sessions: list[dict[str, Any]]) -> None:
    """Render sessions as a Rich table."""
    if not sessions:
        console.print("[dim]No sessions found.[/dim]")
        return
    table = Table(title="Sessions")
    table.add_column("Session ID", style="cyan", no_wrap=True)
    table.add_column("Channel", style="blue")
    table.add_column("Started", style="green")
    table.add_column("Ended", style="yellow")
    table.add_column("Tokens", justify="right")
    for s in sessions:
        table.add_row(
            s.get("session_id", ""),
            s.get("channel", ""),
            s.get("started_at", ""),
            s.get("ended_at") or "-",
            str(s.get("token_count", 0)),
        )
    console.print(table)


def render_users_table(console: Console, users: list[dict[str, Any]]) -> None:
    """Render users as a Rich table."""
    if not users:
        console.print("[dim]No users found.[/dim]")
        return
    table = Table(title="Users")
    table.add_column("User ID", style="cyan")
    table.add_column("Name", style="blue")
    table.add_column("Created", style="dim")
    for u in users:
        table.add_row(u.get("user_id", ""), u.get("name") or "-", u.get("created_at", ""))
    console.print(table)


def render_cron_table(console: Console, jobs: list[dict[str, Any]]) -> None:
    """Render cron jobs as a Rich table."""
    if not jobs:
        console.print("[dim]No cron jobs.[/dim]")
        return
    table = Table(title="Cron Jobs")
    table.add_column("ID", style="cyan")
    table.add_column("User", style="blue")
    table.add_column("Cron", style="yellow")
    table.add_column("Message", style="white")
    table.add_column("Enabled", style="green")
    for j in jobs:
        table.add_row(
            j.get("job_id", ""),
            j.get("user_id", ""),
            j.get("cron_expr", ""),
            j.get("message", ""),
            str(bool(j.get("enabled", False))),
        )
    console.print(table)


def render_skills_table(console: Console, skills: list[dict[str, Any]]) -> None:
    """Render skills as a Rich table."""
    if not skills:
        console.print("[dim]No skills found.[/dim]")
        return
    table = Table(title="Skills")
    table.add_column("Name", style="cyan")
    table.add_column("Description", style="white")
    table.add_column("Always", style="green")
    for s in skills:
        table.add_row(s.get("name", ""), s.get("description", ""), str(s.get("always", False)))
    console.print(table)


def render_config(console: Console, config_dict: dict[str, Any]) -> None:
    """Render sanitized config as a Rich panel."""
    lines: list[str] = []
    for key, value in config_dict.items():
        lines.append(f"[cyan]{key}:[/cyan] {value}")
    console.print(Panel("\n".join(lines), title="Server Config"))


def render_events(console: Console, events: list[dict[str, Any]]) -> None:
    """Render system events."""
    if not events:
        console.print("[dim]No pending events.[/dim]")
        return
    table = Table(title="Events")
    table.add_column("Type", style="cyan")
    table.add_column("Source", style="blue")
    table.add_column("Payload", style="white")
    for e in events:
        table.add_row(
            e.get("event_type", ""),
            e.get("source", ""),
            str(e.get("payload", ""))[:80],
        )
    console.print(table)


def render_history(console: Console, messages: list[dict[str, Any]], n: int = 10) -> None:
    """Render recent chat messages."""
    if not messages:
        console.print("[dim]No messages.[/dim]")
        return
    recent = messages[-n:] if len(messages) > n else messages
    for msg in recent:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if role == "user":
            console.print(f"[bold blue]You:[/bold blue] {content}")
        elif role == "assistant":
            console.print(f"[bold cyan]Bot:[/bold cyan] {content}")
        else:
            console.print(f"[dim]{role}:[/dim] {content}")
