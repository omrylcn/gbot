"""Tests for RBAC permissions module."""

from unittest.mock import MagicMock

import pytest
import yaml

from graphbot.agent.permissions import (
    get_allowed_tools,
    get_context_layers,
    get_default_role,
    get_max_sessions,
    reset_cache,
)
from graphbot.agent.tools import ToolRegistry


@pytest.fixture(autouse=True)
def clear_cache():
    """Reset permissions cache before each test."""
    reset_cache()
    yield
    reset_cache()


@pytest.fixture
def roles_file(tmp_path):
    """Create a test roles.yaml (no tool names — only role->groups)."""
    data = {
        "roles": {
            "owner": {
                "tool_groups": ["web", "memory", "shell"],
                "context_layers": [
                    "identity", "runtime", "role", "agent_memory",
                    "user_context", "events", "session_summary", "skills",
                ],
                "max_sessions": 0,
            },
            "member": {
                "tool_groups": ["web", "memory"],
                "context_layers": [
                    "identity", "runtime", "role", "agent_memory",
                    "user_context", "events", "session_summary", "skills",
                ],
                "max_sessions": 0,
            },
            "guest": {
                "tool_groups": ["web"],
                "context_layers": ["identity", "runtime", "role"],
                "max_sessions": 1,
            },
        },
        "default_role": "guest",
    }
    path = tmp_path / "roles.yaml"
    path.write_text(yaml.dump(data))
    return path


@pytest.fixture
def mock_registry():
    """Create a ToolRegistry with mock tool objects for testing."""
    registry = ToolRegistry()

    def _make_tool(name):
        t = MagicMock()
        t.name = name
        t.description = f"Mock {name}"
        return t

    registry.register_group("web", [_make_tool("web_search"), _make_tool("web_fetch")])
    registry.register_group("memory", [_make_tool("save_user_note"), _make_tool("get_user_context")])
    registry.register_group("shell", [_make_tool("exec_command")])
    return registry


def test_owner_gets_all_tools(roles_file, mock_registry):
    allowed = get_allowed_tools("owner", registry=mock_registry, path=roles_file)
    assert allowed == {"web_search", "web_fetch", "save_user_note", "get_user_context", "exec_command"}


def test_member_no_shell(roles_file, mock_registry):
    allowed = get_allowed_tools("member", registry=mock_registry, path=roles_file)
    assert "exec_command" not in allowed
    assert "web_search" in allowed
    assert "save_user_note" in allowed


def test_guest_only_web(roles_file, mock_registry):
    allowed = get_allowed_tools("guest", registry=mock_registry, path=roles_file)
    assert allowed == {"web_search", "web_fetch"}


def test_unknown_role_empty(roles_file, mock_registry):
    allowed = get_allowed_tools("unknown_role", registry=mock_registry, path=roles_file)
    assert allowed == set()


def test_context_layers_owner(roles_file):
    layers = get_context_layers("owner", path=roles_file)
    assert "agent_memory" in layers
    assert "skills" in layers


def test_context_layers_guest(roles_file):
    layers = get_context_layers("guest", path=roles_file)
    assert layers == {"identity", "runtime", "role"}
    assert "agent_memory" not in layers


def test_max_sessions_guest(roles_file):
    assert get_max_sessions("guest", path=roles_file) == 1


def test_max_sessions_owner(roles_file):
    assert get_max_sessions("owner", path=roles_file) == 0


def test_default_role(roles_file):
    assert get_default_role(roles_file) == "guest"


def test_missing_roles_yaml_returns_none(tmp_path):
    """When roles.yaml is missing, RBAC is disabled (returns None)."""
    allowed = get_allowed_tools("owner", path=tmp_path / "nonexistent.yaml")
    assert allowed is None  # None = no filtering


def test_missing_roles_yaml_context_layers(tmp_path):
    layers = get_context_layers("owner", path=tmp_path / "nonexistent.yaml")
    assert layers is None  # None = all layers


def test_registry_validate_roles(mock_registry):
    """Registry detects unknown groups referenced in roles."""
    roles_data = {
        "roles": {
            "owner": {"tool_groups": ["web", "memory", "nonexistent"]},
        },
    }
    warnings = mock_registry.validate_roles(roles_data)
    assert len(warnings) == 1
    assert "nonexistent" in warnings[0]


def test_registry_groups_summary(mock_registry):
    """Registry returns correct group->tool mapping."""
    summary = mock_registry.get_groups_summary()
    assert "web" in summary
    assert "web_search" in summary["web"]
    assert "memory" in summary
    assert len(summary) == 3
