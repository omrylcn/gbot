"""Tests for graphbot.core.config."""

import yaml
from graphbot.core.config import Config, load_config


def test_defaults():
    cfg = Config()
    assert cfg.assistant.name == "GraphBot"
    assert cfg.assistant.session_token_limit == 30_000
    assert cfg.database.path == "data/graphbot.db"
    assert cfg.rag is None


def test_from_dict():
    cfg = Config(
        assistant={"name": "TestBot", "model": "openai/gpt-4o"},
        providers={"anthropic": {"api_key": "sk-test"}},
    )
    assert cfg.assistant.name == "TestBot"
    assert cfg.providers.anthropic.api_key == "sk-test"


def test_get_api_key():
    cfg = Config(providers={"anthropic": {"api_key": "sk-ant"}})
    assert cfg.get_api_key("anthropic/claude-sonnet") == "sk-ant"
    assert cfg.get_api_key() is None or cfg.get_api_key("unknown") == "sk-ant"


def test_load_yaml(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text(yaml.dump({"assistant": {"name": "YamlBot"}}))
    cfg = load_config(f)
    assert cfg.assistant.name == "YamlBot"


def test_load_missing(tmp_path):
    cfg = load_config(tmp_path / "nope.yaml")
    assert cfg.assistant.name == "GraphBot"


# ── Owner Config ──────────────────────────────────────────


def test_owner_config_none():
    """No owner configured → owner is None, owner_user_id is None."""
    cfg = Config()
    assert cfg.assistant.owner is None
    assert cfg.owner_user_id is None


def test_owner_config_from_dict():
    """Owner parsed from dict correctly."""
    cfg = Config(assistant={"name": "Bot", "owner": {"username": "ali", "name": "Ali"}})
    assert cfg.assistant.owner is not None
    assert cfg.assistant.owner.username == "ali"
    assert cfg.assistant.owner.name == "Ali"
    assert cfg.owner_user_id == "ali"
