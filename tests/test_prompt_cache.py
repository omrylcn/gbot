"""Tests for Faz 22E Step 1 — prompt caching breakpoint injection.

Validates:
- Cache breakpoint is injected for Anthropic / Gemini 2.5 family models
  (when system prompt is long enough).
- Plain string content is used for unsupported providers.
- ``min_chars_to_cache`` is honored (short prompts skip caching).
- ``enabled=False`` disables caching entirely.
- ``excluded_models`` overrides the allow-list prefix match.
"""

from __future__ import annotations

import pytest

from gbot.agent.nodes import _build_system_message, _model_supports_caching
from gbot.core.config.schema import AssistantConfig, Config, PromptCacheConfig


@pytest.fixture
def long_prompt():
    """A prompt above the default min_chars_to_cache (4000)."""
    return "You are GBot. " * 400  # ~5600 chars


@pytest.fixture
def short_prompt():
    return "You are GBot. Be helpful."


def _cfg(model: str, **cache_kwargs) -> Config:
    """Build a Config with a specific model and optional cache overrides."""
    cache = PromptCacheConfig(**cache_kwargs)
    return Config(
        assistant=AssistantConfig(model=model, prompt_cache=cache),
    )


# ── Provider gating ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "model",
    [
        "anthropic/claude-3-5-sonnet-20241022",
        "claude-haiku-4-5-20251001",
        "openrouter/anthropic/claude-sonnet-4-5",
        "openrouter/google/gemini-2.5-flash",
        "gemini-2.5-pro",
    ],
)
def test_supported_models_get_cache_breakpoint(model, long_prompt):
    cfg = _cfg(model)
    msg = _build_system_message(long_prompt, cfg)
    assert isinstance(msg["content"], list)
    assert msg["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert msg["content"][0]["text"] == long_prompt


@pytest.mark.parametrize(
    "model",
    [
        "openai/gpt-4o-mini",
        "openai/gpt-4o",
        "openrouter/openai/gpt-4o-mini",
        "openrouter/google/gemini-3-flash-preview",  # preview, not 2.5
        "groq/llama-3.1-70b",
    ],
)
def test_unsupported_models_get_plain_string(model, long_prompt):
    cfg = _cfg(model)
    msg = _build_system_message(long_prompt, cfg)
    assert msg["content"] == long_prompt
    assert isinstance(msg["content"], str)


def test_excluded_model_overrides_prefix(long_prompt):
    cfg = _cfg(
        "anthropic/claude-experimental",
        excluded_models=["anthropic/claude-experimental"],
    )
    msg = _build_system_message(long_prompt, cfg)
    assert msg["content"] == long_prompt  # plain, not cached


# ── Length gating ───────────────────────────────────────────────


def test_short_prompt_skips_caching(short_prompt):
    cfg = _cfg("anthropic/claude-haiku-4-5")
    msg = _build_system_message(short_prompt, cfg)
    assert msg["content"] == short_prompt


def test_min_chars_threshold_respected():
    cfg = _cfg("anthropic/claude-haiku-4-5", min_chars_to_cache=10)
    msg = _build_system_message("hello world but enough chars", cfg)
    assert isinstance(msg["content"], list)


# ── Disabled flag ───────────────────────────────────────────────


def test_disabled_flag_skips_caching(long_prompt):
    cfg = _cfg("anthropic/claude-haiku-4-5", enabled=False)
    msg = _build_system_message(long_prompt, cfg)
    assert msg["content"] == long_prompt


def test_no_prompt_cache_field_falls_back():
    """Defensive: if some bizarre Config lacks prompt_cache (shouldn't
    happen in production), no crash."""
    from types import SimpleNamespace

    cfg = SimpleNamespace(
        assistant=SimpleNamespace(prompt_cache=None, model="anthropic/claude")
    )
    msg = _build_system_message("hello" * 1000, cfg)
    assert isinstance(msg["content"], str)


# ── _model_supports_caching unit ────────────────────────────────


def test_model_supports_caching_anthropic():
    cache = PromptCacheConfig()
    assert _model_supports_caching("anthropic/claude-haiku-4-5", cache)
    assert _model_supports_caching("openrouter/anthropic/claude-sonnet-4-5", cache)


def test_model_supports_caching_gemini_25():
    cache = PromptCacheConfig()
    assert _model_supports_caching("google/gemini-2.5-flash", cache)
    assert _model_supports_caching("gemini-2.5-pro", cache)


def test_model_supports_caching_rejects_others():
    cache = PromptCacheConfig()
    assert not _model_supports_caching("openai/gpt-4o", cache)
    # gemini-3-* is not on the supported list (preview)
    assert not _model_supports_caching("openrouter/google/gemini-3-flash-preview", cache)
