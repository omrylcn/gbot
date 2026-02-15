"""Tests for gbot_cli.repl + slash_commands (Faz 16)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gbot_cli.client import GraphBotClient
from gbot_cli.repl import REPL
from gbot_cli.slash_commands import SlashCommandRouter


@pytest.fixture
def mock_client():
    client = MagicMock(spec=GraphBotClient)
    client.health.return_value = {"status": "ok"}
    client.chat.return_value = {"response": "Hello!", "session_id": "s1"}
    client.server_status.return_value = {"version": "1.6.0", "model": "test-model", "status": "running"}
    client.list_sessions.return_value = []
    client.session_history.return_value = {"messages": []}
    client.user_context.return_value = {"context_text": "test context"}
    client.admin_config.return_value = {"model": "test"}
    client.admin_skills.return_value = []
    client.admin_cron_jobs.return_value = []
    client.admin_users.return_value = []
    client.get_events.return_value = []
    return client


@pytest.fixture
def repl(mock_client):
    console = MagicMock()
    r = REPL(mock_client, "test_user", console=console)
    return r


def test_slash_help(repl):
    """'/help' prints commands table."""
    router = SlashCommandRouter(repl)
    router.dispatch("/help")
    repl.console.print.assert_called()


def test_slash_exit(repl):
    """'/exit' stops the REPL."""
    router = SlashCommandRouter(repl)
    router.dispatch("/exit")
    assert repl._running is False


def test_slash_session_new(repl):
    """'/session new' resets session_id."""
    repl.session_id = "old-session"
    router = SlashCommandRouter(repl)
    router.dispatch("/session new")
    assert repl.session_id is None


def test_slash_unknown_command(repl):
    """Unknown slash command shows error."""
    router = SlashCommandRouter(repl)
    router.dispatch("/nonexistent")
    # Check error message printed
    args = repl.console.print.call_args[0][0]
    assert "Unknown command" in str(args)


def test_message_dispatch(repl, mock_client):
    """Regular message is sent via client.chat()."""
    repl._send_message("merhaba")
    mock_client.chat.assert_called_once_with("merhaba", session_id=None)


def test_connection_failure():
    """REPL exits if server is unreachable."""
    client = MagicMock(spec=GraphBotClient)
    client.health.side_effect = Exception("Connection refused")
    console = MagicMock()
    r = REPL(client, "user", console=console)
    with pytest.raises(SystemExit):
        r._connect()


def test_banner(repl):
    """Banner is printed on _print_banner()."""
    repl._print_banner()
    # Logo + blank line + panel + hint = 4 print calls
    assert repl.console.print.call_count >= 3


def test_slash_clear(repl):
    """/clear clears the console."""
    router = SlashCommandRouter(repl)
    router.dispatch("/clear")
    repl.console.clear.assert_called_once()
