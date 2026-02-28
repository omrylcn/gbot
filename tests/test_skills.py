"""Tests for Faz 6 — Skills System."""

from __future__ import annotations

from pathlib import Path

import pytest

from graphbot.agent.skills.loader import SkillLoader
from graphbot.core.config import Config
from graphbot.memory.store import MemoryStore


@pytest.fixture
def builtin_dir():
    """Path to real builtin skills."""
    return Path(__file__).parent.parent / "graphbot" / "agent" / "skills" / "builtin"


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def loader(workspace, builtin_dir):
    return SkillLoader(workspace=workspace, builtin_dir=builtin_dir)


def _make_skill(base: Path, name: str, frontmatter: str, body: str) -> Path:
    """Helper to create a SKILL.md file."""
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    path.write_text(f"---\n{frontmatter}---\n\n{body}", encoding="utf-8")
    return path


# ── Frontmatter Parsing ────────────────────────────────────


def test_parse_frontmatter(tmp_path):
    """YAML frontmatter and markdown body are correctly separated."""
    path = tmp_path / "SKILL.md"
    path.write_text(
        "---\nname: test\ndescription: A test skill\nalways: true\n---\n\n# Test\n\nBody here.\n"
    )
    fm, body = SkillLoader._parse_frontmatter(path)
    assert fm["name"] == "test"
    assert fm["description"] == "A test skill"
    assert fm["always"] is True
    assert "Body here." in body


def test_parse_frontmatter_no_yaml(tmp_path):
    """File without frontmatter returns empty dict and full content."""
    path = tmp_path / "SKILL.md"
    path.write_text("# Just markdown\n\nNo frontmatter here.\n")
    fm, body = SkillLoader._parse_frontmatter(path)
    assert fm == {}
    assert "Just markdown" in body


# ── Discovery ──────────────────────────────────────────────


def test_discover_builtin(loader):
    """Builtin skills (weather, summarize) are discovered."""
    skills = loader.discover()
    names = {s.name for s in skills}
    assert "weather" in names
    assert "summarize" in names


def test_discover_workspace_override(workspace, builtin_dir):
    """Workspace skill with same name overrides builtin."""
    _make_skill(
        workspace / "skills", "weather",
        "name: weather\ndescription: Custom weather\nalways: false\n",
        "# My custom weather skill",
    )
    loader = SkillLoader(workspace=workspace, builtin_dir=builtin_dir)
    skills = loader.discover()
    weather = next(s for s in skills if s.name == "weather")
    assert "Custom weather" in weather.description
    assert str(workspace) in str(weather.path)


# ── Load Content ───────────────────────────────────────────


def test_load_content(loader):
    """load_content returns body without frontmatter."""
    content = loader.load_content("weather")
    assert content is not None
    assert "wttr.in" in content
    # Frontmatter should be stripped
    assert "---" not in content.split("\n")[0]


def test_load_content_missing(loader):
    """Non-existent skill returns None."""
    assert loader.load_content("nonexistent") is None


# ── Always-On ──────────────────────────────────────────────


def test_always_on(workspace, builtin_dir):
    """Only always=true skills with met requirements are returned."""
    _make_skill(
        workspace / "skills", "greeter",
        "name: greeter\ndescription: Greets users\nalways: true\n",
        "# Greeter\n\nSay hello!",
    )
    loader = SkillLoader(workspace=workspace, builtin_dir=builtin_dir)
    always = loader.get_always_on()
    names = {s.name for s in always}
    assert "greeter" in names
    # weather and summarize have always=false
    assert "weather" not in names
    assert "summarize" not in names


# ── Requirements Check ─────────────────────────────────────


def test_requirements_check_bins():
    """Missing binary → False, existing binary → True."""
    # 'ls' should exist on any system
    assert SkillLoader._check_requirements({"requires": {"bins": ["ls"]}}) is True
    # Nonexistent binary
    assert SkillLoader._check_requirements({"requires": {"bins": ["xyznotreal123"]}}) is False


def test_requirements_check_env(monkeypatch):
    """Missing env var → False, set env var → True."""
    monkeypatch.setenv("TEST_SKILL_KEY", "abc")
    assert SkillLoader._check_requirements({"requires": {"env": ["TEST_SKILL_KEY"]}}) is True
    monkeypatch.delenv("TEST_SKILL_KEY")
    assert SkillLoader._check_requirements({"requires": {"env": ["TEST_SKILL_KEY"]}}) is False


# ── Build Index ────────────────────────────────────────────


def test_build_index(loader):
    """XML index contains all skills with correct format."""
    index = loader.build_index()
    assert "<skills>" in index
    assert "</skills>" in index
    assert "<name>weather</name>" in index
    assert "<name>summarize</name>" in index
    assert 'available="' in index


# ── Context Integration ────────────────────────────────────


def test_context_integration(tmp_path):
    """ContextBuilder includes skills layers in system prompt."""
    from graphbot.agent.context import ContextBuilder

    ws = tmp_path / "workspace"
    ws.mkdir()
    # Create an always-on skill in workspace
    skills_dir = ws / "skills" / "greet"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\nname: greet\ndescription: Greeting skill\nalways: true\n---\n\n# Greet\n\nSay hello!\n"
    )

    cfg = Config(assistant={"workspace": str(ws)})
    db = MemoryStore(str(tmp_path / "test.db"))

    builder = ContextBuilder(cfg, db)
    prompt = builder.build("test-user")

    # Layer 5: always-on skill content
    assert "Active Skills" in prompt
    assert "Say hello!" in prompt
    # Layer 6: skills index
    assert "Available Skills" in prompt
    assert "<skills>" in prompt
    assert "<name>greet</name>" in prompt
