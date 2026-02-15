"""Slash command router for the interactive REPL."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.columns import Columns
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from gbot_cli.client import APIError
from gbot_cli.output import (
    render_config,
    render_cron_table,
    render_events,
    render_history,
    render_sessions_table,
    render_skills_table,
    render_users_table,
)

if TYPE_CHECKING:
    from gbot_cli.repl import REPL


class SlashCommandRouter:
    """Dispatch /commands inside the interactive REPL."""

    def __init__(self, repl: REPL):
        self._repl = repl
        self._handlers: dict[str, callable] = {
            "/help": self._help,
            "/exit": self._exit,
            "/quit": self._exit,
            "/status": self._status,
            "/session": self._session,
            "/model": self._model,
            "/history": self._history,
            "/context": self._context,
            "/config": self._config,
            "/skill": self._skill,
            "/cron": self._cron,
            "/user": self._user,
            "/events": self._events,
            "/clear": self._clear,
        }

    def dispatch(self, text: str) -> None:
        """Parse and dispatch a slash command."""
        stripped = text.strip()

        # Just "/" alone → show help
        if stripped == "/":
            self._help("")
            return

        parts = stripped.split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        handler = self._handlers.get(cmd)
        if handler is None:
            self._repl.console.print(f"[red]Unknown command:[/red] {cmd}  (type /help)")
            return
        try:
            handler(args)
        except APIError as e:
            self._repl.console.print(f"[red]API error:[/red] {e.detail}")

    # ── Handlers ─────────────────────────────────────────────

    def _help(self, _args: str) -> None:
        c = self._repl.console

        def _section(title: str, cmds: list[tuple[str, str]]) -> Panel:
            lines = Text()
            for i, (cmd, desc) in enumerate(cmds):
                if i > 0:
                    lines.append("\n")
                lines.append(f"  {cmd:<28}", style="bold cyan")
                lines.append(desc, style="dim")
            return Panel(lines, title=f"[bold]{title}[/bold]", border_style="blue", padding=(0, 1))

        chat_panel = _section("Chat", [
            ("/history [n]", "Recent messages"),
            ("/context", "User context"),
            ("/clear", "Clear screen"),
        ])
        session_panel = _section("Session", [
            ("/session info", "Current session"),
            ("/session new", "Start new session"),
            ("/session list", "List all sessions"),
            ("/session end", "End current session"),
        ])
        admin_panel = _section("Admin", [
            ("/status", "Server status"),
            ("/model", "Active model"),
            ("/config", "Server config"),
            ("/user", "User list"),
            ("/skill", "Skill list"),
            ("/cron [list|remove <id>]", "Cron jobs"),
            ("/events", "Pending events"),
        ])
        other_panel = _section("Other", [
            ("/help", "Show this help"),
            ("/exit", "Quit"),
        ])

        c.print()
        c.print(Columns([chat_panel, session_panel], equal=True, expand=True))
        c.print(Columns([admin_panel, other_panel], equal=True, expand=True))
        c.print()

    def _exit(self, _args: str) -> None:
        self._repl.stop()

    def _status(self, _args: str) -> None:
        data = self._repl.client.server_status()
        table = Table(title="Server Status")
        table.add_column("Key", style="cyan")
        table.add_column("Value", style="green")
        for k, v in data.items():
            table.add_row(str(k), str(v))
        self._repl.console.print(table)

    def _session(self, args: str) -> None:
        sub = args.strip().lower() if args else "info"

        if sub == "new":
            self._repl.session_id = None
            self._repl.console.print("[green]New session started.[/green]")

        elif sub == "list":
            sessions = self._repl.client.list_sessions(self._repl.user_id)
            render_sessions_table(self._repl.console, sessions)

        elif sub == "end":
            if self._repl.session_id:
                self._repl.client.end_session(self._repl.session_id)
                self._repl.console.print(f"[yellow]Session ended:[/yellow] {self._repl.session_id}")
                self._repl.session_id = None
            else:
                self._repl.console.print("[dim]No active session.[/dim]")

        elif sub == "info":
            sid = self._repl.session_id or "(none)"
            self._repl.console.print(f"[cyan]Current session:[/cyan] {sid}")

        else:
            self._repl.console.print(f"[red]Unknown session subcommand:[/red] {sub}")

    def _model(self, _args: str) -> None:
        try:
            data = self._repl.client.server_status()
            self._repl.console.print(f"[cyan]Model:[/cyan] {data.get('model', 'unknown')}")
        except APIError:
            self._repl.console.print("[dim]Could not retrieve model info.[/dim]")

    def _history(self, args: str) -> None:
        n = int(args) if args.strip().isdigit() else 10
        if not self._repl.session_id:
            self._repl.console.print("[dim]No active session.[/dim]")
            return
        data = self._repl.client.session_history(self._repl.session_id)
        render_history(self._repl.console, data.get("messages", []), n=n)

    def _context(self, _args: str) -> None:
        data = self._repl.client.user_context(self._repl.user_id)
        from rich.markdown import Markdown

        self._repl.console.print(Markdown(data.get("context_text", "")))

    def _config(self, _args: str) -> None:
        data = self._repl.client.admin_config()
        render_config(self._repl.console, data)

    def _skill(self, _args: str) -> None:
        skills = self._repl.client.admin_skills()
        render_skills_table(self._repl.console, skills)

    def _cron(self, args: str) -> None:
        parts = args.strip().split(maxsplit=1)
        sub = parts[0].lower() if parts else "list"

        if sub == "list" or not sub:
            jobs = self._repl.client.admin_cron_jobs()
            render_cron_table(self._repl.console, jobs)
        elif sub == "remove" and len(parts) > 1:
            job_id = parts[1].strip()
            self._repl.client.admin_remove_cron(job_id)
            self._repl.console.print(f"[green]Removed cron:[/green] {job_id}")
        else:
            self._repl.console.print("[red]Usage:[/red] /cron [list|remove <id>]")

    def _user(self, _args: str) -> None:
        users = self._repl.client.admin_users()
        render_users_table(self._repl.console, users)

    def _events(self, _args: str) -> None:
        events = self._repl.client.get_events(self._repl.user_id)
        render_events(self._repl.console, events)

    def _clear(self, _args: str) -> None:
        self._repl.console.clear()
