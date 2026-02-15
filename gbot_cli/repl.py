"""Interactive REPL — Rich markdown output, slash commands, API-backed chat."""

from __future__ import annotations

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from graphbot import __version__
from gbot_cli.client import APIError, GraphBotClient
from gbot_cli.slash_commands import SlashCommandRouter

_LOGO = r"""
       ┌───────┐
       │ ◉   ◉ │
       │  ───  │
       └───┬───┘
       ┌───┴───┐
       │ gbot  │
       └───────┘
        _           _
   __ _| |__   ___ | |_
  / _` | '_ \ / _ \| __|
 | (_| | |_) | (_) | |_
  \__, |_.__/ \___/ \__|
  |___/"""

# Slash command definitions: (command, description)
_SLASH_COMMANDS: list[tuple[str, str]] = [
    ("/help", "Show commands"),
    ("/exit", "Quit"),
    ("/quit", "Quit"),
    ("/status", "Server status"),
    ("/session info", "Current session"),
    ("/session new", "Start new session"),
    ("/session list", "List sessions"),
    ("/session end", "End session"),
    ("/model", "Active model"),
    ("/history", "Recent messages"),
    ("/context", "User context"),
    ("/config", "Server config"),
    ("/skill", "List skills"),
    ("/cron list", "List cron jobs"),
    ("/cron remove", "Remove cron job"),
    ("/user", "User list"),
    ("/events", "Pending events"),
    ("/clear", "Clear screen"),
]


class SlashCompleter(Completer):
    """Auto-complete slash commands as user types."""

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor.lstrip()
        if not text.startswith("/"):
            return

        for cmd, desc in _SLASH_COMMANDS:
            if cmd.startswith(text):
                yield Completion(
                    cmd,
                    start_position=-len(text),
                    display=cmd,
                    display_meta=desc,
                )


class REPL:
    """API-connected interactive chat shell."""

    def __init__(
        self,
        client: GraphBotClient,
        user_id: str,
        console: Console | None = None,
    ):
        self.client = client
        self.user_id = user_id
        self.console = console or Console()
        self.session_id: str | None = None
        self._running = False
        self._slash = SlashCommandRouter(self)
        self._server_info: dict | None = None
        self._prompt_session = PromptSession(
            completer=SlashCompleter(),
            complete_while_typing=True,
        )

    def start(self) -> None:
        """Run the REPL loop."""
        self._connect()
        self._print_banner()
        self._running = True

        while self._running:
            try:
                user_input = self._prompt_session.prompt(
                    HTML("<b><ansiblue>You</ansiblue></b>: "),
                )
            except (KeyboardInterrupt, EOFError):
                self.console.print("\nBye!")
                break

            text = user_input.strip()
            if not text:
                continue

            if text.startswith("/"):
                self._slash.dispatch(text)
            else:
                self._send_message(text)

    def stop(self) -> None:
        """Signal the REPL to exit."""
        self._running = False
        self.console.print("Bye!")

    def _connect(self) -> None:
        """Health check — fail fast if server is unreachable."""
        try:
            self.client.health()
            try:
                self._server_info = self.client.server_status()
            except Exception:
                self._server_info = None
        except Exception as e:
            self.console.print(f"[red]Cannot connect to server:[/red] {e}")
            raise SystemExit(1) from e

    def _print_banner(self) -> None:
        """Display welcome banner with logo and server info."""
        self.console.print(f"[bold blue]{_LOGO}[/bold blue]")
        self.console.print()

        model = "unknown"
        server_version = __version__
        status = "connected"
        if self._server_info:
            model = self._server_info.get("model", model)
            server_version = self._server_info.get("version", server_version)

        info = (
            f"  [bold]Version:[/bold]  {server_version}\n"
            f"  [bold]Model:[/bold]    [cyan]{model}[/cyan]\n"
            f"  [bold]User:[/bold]     [green]{self.user_id}[/green]\n"
            f"  [bold]Status:[/bold]   [green]{status}[/green]"
        )
        self.console.print(Panel(info, border_style="blue", padding=(0, 1)))
        self.console.print(
            "  Type a message to chat, [bold]/[/bold] for commands, "
            "[bold]/exit[/bold] to quit.\n"
        )

    def _send_message(self, text: str) -> None:
        """Send chat message via API with a spinner."""
        try:
            with self.console.status("Thinking..."):
                data = self.client.chat(text, session_id=self.session_id)
            self.session_id = data.get("session_id", self.session_id)
            response = data.get("response", "")
            self.console.print()
            self.console.print(Markdown(response))
            self.console.print()
        except APIError as e:
            self.console.print(f"\n[red]Error:[/red] {e.detail}\n")
