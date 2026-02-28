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
        c = self._repl.console

        # Try new /admin/stats first, fallback to old /admin/status
        try:
            data = self._repl.client.admin_stats()
        except APIError:
            data = self._repl.client.server_status()
            table = Table(title="Server Status")
            table.add_column("Key", style="cyan")
            table.add_column("Value", style="green")
            for k, v in data.items():
                table.add_row(str(k), str(v))
            c.print(table)
            return

        sys_info = data.get("system", {})
        ctx = data.get("context", {})
        tools = data.get("tools", {})
        sessions = data.get("sessions", {})
        db_data = data.get("data", {})

        # ── System ──
        sys_table = Table(show_header=False, box=None, padding=(0, 2))
        sys_table.add_column(style="dim")
        sys_table.add_column(style="bold")
        sys_table.add_row("Version", sys_info.get("version", "?"))
        sys_table.add_row("Model", sys_info.get("model", "?"))
        thinking = "on" if sys_info.get("thinking") else "off"
        sys_table.add_row("Thinking", thinking)
        sys_table.add_row("Token Limit", f"{sys_info.get('session_token_limit', 0):,}")
        c.print(Panel(sys_table, title="System", border_style="blue"))

        # ── Context ──
        layers = ctx.get("layers", [])
        if layers:
            ctx_table = Table(box=None, padding=(0, 2))
            ctx_table.add_column("Layer", style="cyan")
            ctx_table.add_column("Tokens", justify="right", style="green")
            ctx_table.add_column("Budget", justify="right", style="yellow")
            ctx_table.add_column("", style="dim")
            for layer in layers:
                tokens = layer.get("tokens", 0)
                budget = layer.get("budget", 0)
                bar_len = min(tokens // 20, 30)
                truncated = layer.get("truncated", False)
                bar = Text("█" * bar_len, style="red" if truncated else "green")
                budget_str = str(budget) if budget else "-"
                ctx_table.add_row(layer["layer"], f"{tokens:,}", budget_str, bar)
            ctx_table.add_section()
            ctx_table.add_row("TOTAL", f"{ctx.get('total_tokens', 0):,}", "", "")
            c.print(Panel(ctx_table, title="Context", border_style="green"))

        # ── Tools + Data (compact) ──
        info_table = Table(show_header=False, box=None, padding=(0, 2))
        info_table.add_column(style="dim")
        info_table.add_column(justify="right", style="bold")
        info_table.add_row("Tools", f"{tools.get('available', 0)} available ({tools.get('total', 0)} registered)")
        info_table.add_row("Sessions", f"{sessions.get('active', 0)} active / {sessions.get('total', 0)} total")
        info_table.add_row("Total Tokens", f"{sessions.get('total_tokens', 0):,}")
        info_table.add_row("Messages", f"{db_data.get('messages', 0):,}")
        info_table.add_row("Users", str(db_data.get("users", 0)))
        info_table.add_row("Cron Jobs", str(db_data.get("cron_jobs", 0)))
        c.print(Panel(info_table, title="Overview", border_style="magenta"))

        # ── Active Session ──
        sid = self._repl.session_id
        if sid:
            try:
                ss = self._repl.client.session_stats(sid)
                msgs = ss.get("messages", {})
                toks = ss.get("tokens", {})
                pct = toks.get("percent", 0)
                used = toks.get("used", 0)
                limit = toks.get("limit", 30000)

                bar_width = 30
                filled = int(bar_width * pct / 100)
                bar_color = "green" if pct < 60 else ("yellow" if pct < 85 else "red")
                bar = Text("█" * filled + "░" * (bar_width - filled), style=bar_color)

                sess_table = Table(show_header=False, box=None, padding=(0, 2))
                sess_table.add_column(style="dim")
                sess_table.add_column(style="bold")
                sess_table.add_row("Session", sid[:12] + "...")
                sess_table.add_row(
                    "Messages",
                    f"{msgs.get('total', 0)} ({msgs.get('user', 0)} user, "
                    f"{msgs.get('assistant', 0)} assistant, {msgs.get('tool_calls', 0)} tool)",
                )
                sess_table.add_row("Tokens", f"{used:,} / {limit:,} ({pct:.0f}%)")
                sess_table.add_row("Progress", bar)
                c.print(Panel(sess_table, title="Current Session", border_style="cyan"))
            except APIError:
                pass

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
