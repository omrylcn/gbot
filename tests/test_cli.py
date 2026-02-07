"""Tests for graphbot.cli (Faz 8)."""

from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from graphbot.cli.commands import app

runner = CliRunner()

_PATCH_CONFIG = "graphbot.core.config.loader.load_config"
_PATCH_STORE = "graphbot.memory.store.MemoryStore"
_PATCH_RUNNER = "graphbot.agent.runner.GraphRunner"


def test_cli_help():
    """--help works and shows command names."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "run" in result.output
    assert "chat" in result.output
    assert "status" in result.output
    assert "cron" in result.output


def test_chat_single_message(tmp_path):
    """chat -m sends a single message via runner.process."""
    mock_runner = MagicMock()
    mock_runner.process = AsyncMock(return_value=("Hello from bot", "cli:default"))

    fake_config = MagicMock()
    fake_config.database.path = str(tmp_path / "test.db")

    fake_db = MagicMock()

    with (
        patch(_PATCH_CONFIG, return_value=fake_config),
        patch(_PATCH_STORE, return_value=fake_db),
        patch(_PATCH_RUNNER, return_value=mock_runner),
    ):
        result = runner.invoke(app, ["chat", "-m", "merhaba"])

    assert result.exit_code == 0
    assert "Hello from bot" in result.output
    mock_runner.process.assert_called_once_with("cli_user", "cli", "merhaba", "cli:default")


def test_status_output(tmp_path):
    """status shows model name and DB path."""
    fake_config = MagicMock()
    fake_config.assistant.model = "anthropic/claude-test"
    fake_config.database.path = str(tmp_path / "test.db")

    fake_conn = MagicMock()
    fake_conn.execute.return_value.fetchone.return_value = [0]
    fake_conn.__enter__ = MagicMock(return_value=fake_conn)
    fake_conn.__exit__ = MagicMock(return_value=False)

    fake_db = MagicMock()
    fake_db._get_conn.return_value = fake_conn

    with (
        patch(_PATCH_CONFIG, return_value=fake_config),
        patch(_PATCH_STORE, return_value=fake_db),
    ):
        result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "claude-test" in result.output
    assert "DB Path" in result.output


def test_cron_list_empty(tmp_path):
    """cron list shows message when no jobs exist."""
    fake_config = MagicMock()
    fake_config.database.path = str(tmp_path / "test.db")

    fake_db = MagicMock()
    fake_db.get_cron_jobs.return_value = []

    with (
        patch(_PATCH_CONFIG, return_value=fake_config),
        patch(_PATCH_STORE, return_value=fake_db),
    ):
        result = runner.invoke(app, ["cron", "list"])

    assert result.exit_code == 0
    assert "No cron jobs found" in result.output


def test_cron_remove(tmp_path):
    """cron remove calls db.remove_cron_job."""
    fake_config = MagicMock()
    fake_config.database.path = str(tmp_path / "test.db")

    fake_db = MagicMock()

    with (
        patch(_PATCH_CONFIG, return_value=fake_config),
        patch(_PATCH_STORE, return_value=fake_db),
    ):
        result = runner.invoke(app, ["cron", "remove", "job-123"])

    assert result.exit_code == 0
    assert "job-123" in result.output
    fake_db.remove_cron_job.assert_called_once_with("job-123")


def test_main_module():
    """python -m graphbot entry point is importable."""
    from graphbot.__main__ import app as main_app

    assert main_app is app
