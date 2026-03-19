"""Tests for ContextBuilder.build_layers() and ContextService."""

import pytest
import gbot.agent.profiles as profiles_mod

from gbot.agent.context import ContextBuilder, ContextService, LayerResult
from gbot.core.config.schema import Config
from gbot.memory.store import MemoryStore


@pytest.fixture(autouse=True)
def isolate_profiles(monkeypatch):
    """Prevent tests from loading real agents.yaml."""
    profiles_mod.reset_cache()
    monkeypatch.setattr(profiles_mod, "_PROFILES_DATA", {})
    yield
    profiles_mod.reset_cache()


def _make_config(tmp_path, **overrides):
    ws = tmp_path / "workspace"
    ws.mkdir(exist_ok=True)
    assistant_cfg = {"system_prompt": "TestBot.", "workspace": str(ws),
                     "owner": {"username": "testuser", "name": "Test"}}
    assistant_cfg.update(overrides)
    return Config(
        assistant=assistant_cfg,
        database={"path": str(tmp_path / "test.db")},
    )


def _make_db(tmp_path):
    db = MemoryStore(str(tmp_path / "test.db"))
    db.get_or_create_user("testuser", name="Test")
    return db


# ── ContextBuilder.build_layers() ─────────────────────


def test_build_layers_returns_dict(tmp_path):
    """build_layers returns dict of LayerResult objects."""
    config = _make_config(tmp_path)
    db = _make_db(tmp_path)
    builder = ContextBuilder(config, db)
    layers = builder.build_layers("testuser")
    assert isinstance(layers, dict)
    assert all(isinstance(lr, LayerResult) for lr in layers.values())


def test_build_layers_has_identity_and_runtime(tmp_path):
    """build_layers includes identity and runtime layers."""
    config = _make_config(tmp_path)
    db = _make_db(tmp_path)
    builder = ContextBuilder(config, db)
    layers = builder.build_layers("testuser")
    assert "identity" in layers
    assert "runtime" in layers
    assert "TestBot." in layers["identity"].content


def test_build_layers_content_matches_build(tmp_path):
    """Joining layer contents should match build() output."""
    config = _make_config(tmp_path)
    db = _make_db(tmp_path)
    builder = ContextBuilder(config, db)

    layers = builder.build_layers("testuser", mark_delivered=True)
    joined = "\n\n---\n\n".join(lr.content for lr in layers.values() if lr.content)
    built = builder.build("testuser")
    assert joined == built


def test_build_layers_respects_rbac(tmp_path):
    """Only allowed layers appear when context_layers is restricted."""
    config = _make_config(tmp_path)
    db = _make_db(tmp_path)
    builder = ContextBuilder(config, db)
    layers = builder.build_layers("testuser", context_layers={"identity"})
    assert "identity" in layers
    assert "runtime" not in layers
    assert "agent_memory" not in layers


def test_build_layers_no_side_effects(tmp_path):
    """Build layers with mark_delivered flag does not cause errors."""
    config = _make_config(tmp_path)
    db = _make_db(tmp_path)
    builder = ContextBuilder(config, db)

    # Both modes should work without errors
    layers_preview = builder.build_layers("testuser", mark_delivered=False)
    layers_build = builder.build_layers("testuser", mark_delivered=True)
    assert isinstance(layers_preview, dict)
    assert isinstance(layers_build, dict)


def test_context_stats_backward_compat(tmp_path):
    """get_context_stats returns same format as before."""
    config = _make_config(tmp_path)
    db = _make_db(tmp_path)
    builder = ContextBuilder(config, db)
    stats = builder.get_context_stats("testuser")
    assert "layers" in stats
    assert "total_chars" in stats
    assert "total_tokens" in stats
    assert all("layer" in d and "chars" in d for d in stats["layers"])


# ── ContextService ─────────────────────────────────────


def test_service_get_layers(tmp_path):
    """ContextService.get_layers returns LayerResult dict."""
    config = _make_config(tmp_path)
    db = _make_db(tmp_path)
    svc = ContextService(config, db)
    layers = svc.get_layers("testuser")
    assert isinstance(layers, dict)
    assert "identity" in layers


def test_service_preview(tmp_path):
    """ContextService.preview returns rendered string."""
    config = _make_config(tmp_path)
    db = _make_db(tmp_path)
    svc = ContextService(config, db)
    preview = svc.preview("testuser")
    assert isinstance(preview, str)
    assert "TestBot." in preview


def test_service_planner_context(tmp_path):
    """get_planner_context returns layer dict with identity."""
    config = _make_config(tmp_path)
    db = _make_db(tmp_path)
    svc = ContextService(config, db)
    layers = svc.get_planner_context(tool_catalog="test tools")
    assert isinstance(layers, dict)
    assert "identity" in layers
    assert layers["identity"].chars > 0


def test_service_light_context(tmp_path):
    """get_light_context returns layers with task_prompt."""
    config = _make_config(tmp_path)
    db = _make_db(tmp_path)
    svc = ContextService(config, db)
    layers = svc.get_light_context(task_prompt="Do something")
    assert isinstance(layers, dict)
    assert "identity" in layers
    assert "task_prompt" in layers
    assert "Do something" in layers["task_prompt"].content


def test_service_override_replaces_content(tmp_path):
    """set_override with content replaces layer content."""
    config = _make_config(tmp_path)
    db = _make_db(tmp_path)
    svc = ContextService(config, db)

    svc.set_override("main", "identity", content="Custom identity")
    layers = svc.get_layers("testuser")
    assert layers["identity"].content == "Custom identity"


def test_service_override_disables_layer(tmp_path):
    """set_override with enabled=False disables layer."""
    config = _make_config(tmp_path)
    db = _make_db(tmp_path)
    svc = ContextService(config, db)

    svc.set_override("main", "identity", enabled=False)
    layers = svc.get_layers("testuser")
    assert layers["identity"].enabled is False

    # Preview should skip disabled layers
    preview = svc.preview("testuser")
    assert "TestBot." not in preview


def test_service_clear_override(tmp_path):
    """clear_override removes the override."""
    config = _make_config(tmp_path)
    db = _make_db(tmp_path)
    svc = ContextService(config, db)

    svc.set_override("main", "identity", content="Override")
    svc.clear_override("main", "identity")
    layers = svc.get_layers("testuser")
    assert layers["identity"].content != "Override"


def test_service_list_profiles(tmp_path):
    """list_profiles returns profile list."""
    config = _make_config(tmp_path)
    db = _make_db(tmp_path)
    svc = ContextService(config, db)
    profiles = svc.list_profiles()
    # With empty profiles data, returns empty list
    assert isinstance(profiles, list)
