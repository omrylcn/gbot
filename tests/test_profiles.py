"""Tests for agent profiles module."""

import pytest
import yaml

from gbot.agent.profiles import (
    get_agent_md,
    get_agent_skills,
    get_profile,
    get_template_vars,
    reset_cache,
)


@pytest.fixture(autouse=True)
def clear_cache():
    """Reset profiles cache before each test."""
    reset_cache()
    yield
    reset_cache()


@pytest.fixture
def agents_file(tmp_path):
    """Create a test agents.yaml."""
    data = {
        "agents": {
            "main": {
                "agent_md": str(tmp_path / "workspace" / "AGENT.md"),
                "skills": ["*"],
            },
            "planner": {
                "agent_md": str(tmp_path / "workspace" / "agents" / "planner" / "AGENT.md"),
                "skills": [],
                "template_vars": ["tool_catalog", "extra_examples"],
            },
            "light": {
                "agent_md": str(tmp_path / "workspace" / "agents" / "light" / "AGENT.md"),
                "skills": [],
            },
        }
    }
    path = tmp_path / "agents.yaml"
    path.write_text(yaml.dump(data), encoding="utf-8")
    return path


@pytest.fixture
def agent_md_files(tmp_path):
    """Create test AGENT.md files."""
    main_md = tmp_path / "workspace" / "AGENT.md"
    main_md.parent.mkdir(parents=True, exist_ok=True)
    main_md.write_text("# Main Agent\nYou are the main agent.", encoding="utf-8")

    planner_md = tmp_path / "workspace" / "agents" / "planner" / "AGENT.md"
    planner_md.parent.mkdir(parents=True, exist_ok=True)
    planner_md.write_text("# Planner\nYou plan tasks. {tool_catalog}", encoding="utf-8")

    light_md = tmp_path / "workspace" / "agents" / "light" / "AGENT.md"
    light_md.parent.mkdir(parents=True, exist_ok=True)
    light_md.write_text("# Light Agent\nMinimal context.", encoding="utf-8")

    return {"main": main_md, "planner": planner_md, "light": light_md}


def test_get_profile_existing(agents_file):
    """Known profile returns its config."""
    profile = get_profile("main", agents_file)
    assert profile["skills"] == ["*"]


def test_get_profile_unknown(agents_file):
    """Unknown profile returns empty dict."""
    profile = get_profile("unknown", agents_file)
    assert profile == {}


def test_get_profile_missing_file(tmp_path):
    """Missing agents.yaml returns empty dict for any profile."""
    profile = get_profile("main", tmp_path / "nonexistent.yaml")
    assert profile == {}


def test_get_agent_md(agents_file, agent_md_files):
    """AGENT.md content is loaded from profile path."""
    content = get_agent_md("main", agents_file)
    assert content is not None
    assert "Main Agent" in content


def test_get_agent_md_missing_file(agents_file):
    """Returns None when AGENT.md file does not exist."""
    content = get_agent_md("main", agents_file)
    assert content is None


def test_get_agent_md_no_profile(agents_file):
    """Returns None for unknown profile."""
    content = get_agent_md("unknown", agents_file)
    assert content is None


def test_get_agent_skills(agents_file):
    """Skills list returned correctly."""
    assert get_agent_skills("main", agents_file) == ["*"]
    assert get_agent_skills("planner", agents_file) == []


def test_get_agent_skills_unknown(agents_file):
    """Unknown profile returns empty skills."""
    assert get_agent_skills("unknown", agents_file) == []


def test_get_template_vars(agents_file):
    """Template vars returned for planner profile."""
    vars_ = get_template_vars("planner", agents_file)
    assert "tool_catalog" in vars_
    assert "extra_examples" in vars_


def test_get_template_vars_no_vars(agents_file):
    """Profile without template_vars returns empty list."""
    assert get_template_vars("main", agents_file) == []


def test_cache_works(agents_file):
    """Second call uses cached data (no file re-read)."""
    get_profile("main", agents_file)
    # Delete the file — cached data should still work
    agents_file.unlink()
    profile = get_profile("main")
    assert profile["skills"] == ["*"]
