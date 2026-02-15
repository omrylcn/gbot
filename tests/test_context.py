"""Tests for ContextBuilder — persona, roles, token budget (Faz 12)."""

from graphbot.agent.context import ContextBuilder
from graphbot.core.config.schema import Config
from graphbot.memory.store import MemoryStore


# ── Helpers ─────────────────────────────────────────────────


def _make_builder(tmp_path, *, persona=None, roles=None, priorities=None,
                  system_prompt=None, prompt_template=None, agent_md=None):
    """Create a ContextBuilder with given config overrides."""
    ws = tmp_path / "workspace"
    ws.mkdir(exist_ok=True)

    if agent_md is not None:
        (ws / "AGENT.md").write_text(agent_md, encoding="utf-8")

    assistant_cfg = {"system_prompt": system_prompt or "TestBot.", "workspace": str(ws),
                     "owner": {"username": "testuser", "name": "Test"}}
    if persona:
        assistant_cfg["persona"] = persona
    if roles:
        assistant_cfg["roles"] = roles
    if priorities:
        assistant_cfg["context_priorities"] = priorities
    if prompt_template:
        assistant_cfg["prompt_template"] = prompt_template
    # Clear system_prompt if we want to test persona/AGENT.md fallback
    if agent_md is not None or persona:
        assistant_cfg["system_prompt"] = None

    config = Config(
        assistant=assistant_cfg,
        database={"path": str(tmp_path / "test.db")},
    )
    db = MemoryStore(str(tmp_path / "test.db"))
    db.get_or_create_user("testuser", name="Test")
    return ContextBuilder(config, db)


# ── Tests ───────────────────────────────────────────────────


def test_default_identity_no_persona(tmp_path):
    """Without persona/AGENT.md, system_prompt is used."""
    builder = _make_builder(tmp_path)
    prompt = builder.build("testuser")
    assert "TestBot." in prompt


def test_persona_builds_identity(tmp_path):
    """PersonaConfig generates structured identity when no system_prompt or AGENT.md."""
    builder = _make_builder(
        tmp_path,
        persona={"name": "MyBot", "tone": "friendly", "language": "tr",
                 "constraints": ["Always be kind"]},
    )
    prompt = builder.build("testuser")
    assert "MyBot" in prompt
    assert "friendly" in prompt
    assert "tr" in prompt
    assert "Always be kind" in prompt


def test_persona_constraints_appended_to_agent_md(tmp_path):
    """When AGENT.md exists and persona has constraints, constraints are appended."""
    builder = _make_builder(
        tmp_path,
        agent_md="# I am AgentMD bot",
        persona={"constraints": ["No swearing", "Be concise"]},
    )
    prompt = builder.build("testuser")
    assert "AgentMD bot" in prompt
    assert "No swearing" in prompt
    assert "Be concise" in prompt
    assert "Additional Constraints" in prompt


def test_role_injection(tmp_path):
    """When roles are configured, Current Role section appears in prompt."""
    builder = _make_builder(
        tmp_path,
        roles={"default": "analyst", "available": {"analyst": "Data analysis expert"}},
    )
    prompt = builder.build("testuser")
    assert "Current Role" in prompt
    assert "analyst" in prompt
    assert "Data analysis expert" in prompt


def test_role_default_fallback(tmp_path):
    """When available is empty but default is set, default role name is used."""
    builder = _make_builder(
        tmp_path,
        roles={"default": "general assistant"},
    )
    prompt = builder.build("testuser")
    assert "general assistant" in prompt


def test_context_priorities_truncation(tmp_path):
    """Token budget truncates content that exceeds the limit."""
    builder = _make_builder(
        tmp_path,
        priorities={"identity": 5, "agent_memory": 500, "user_context": 1500,
                     "session_summary": 500, "skills": 1000},
    )
    # Identity with budget=5 → 20 chars max, "TestBot." is 8 chars → fits
    # Let's test _truncate directly
    result = builder._truncate("A" * 100, token_budget=5)
    assert len(result) < 100
    assert "[...truncated]" in result


def test_custom_prompt_template(tmp_path):
    """prompt_template loads from file with variable substitution."""
    template_file = tmp_path / "my_prompt.txt"
    template_file.write_text("Hello, I am {name}. Tone: {tone}.", encoding="utf-8")

    builder = _make_builder(
        tmp_path,
        prompt_template=str(template_file),
        persona={"name": "TemplateBot", "tone": "serious"},
    )
    prompt = builder.build("testuser")
    assert "TemplateBot" in prompt
    assert "serious" in prompt


def test_backward_compat_no_config(tmp_path):
    """Without any new config, existing behavior is preserved."""
    config = Config(
        assistant={"system_prompt": "Legacy prompt.", "workspace": str(tmp_path),
                    "owner": {"username": "u1", "name": "U"}},
        database={"path": str(tmp_path / "test.db")},
    )
    db = MemoryStore(str(tmp_path / "test.db"))
    db.get_or_create_user("u1", name="U")
    builder = ContextBuilder(config, db)
    prompt = builder.build("u1")
    assert "Legacy prompt." in prompt
    assert "Runtime" in prompt
    # No role section
    assert "Current Role" not in prompt


def test_role_override_param(tmp_path):
    """Role can be overridden via build() parameter."""
    builder = _make_builder(
        tmp_path,
        roles={"default": "general", "available": {
            "general": "General assistant",
            "coder": "Software engineer",
        }},
    )
    prompt = builder.build("testuser", role="coder")
    assert "coder" in prompt
    assert "Software engineer" in prompt
