"""Tests for the LLM provider — OpenRouter SDK only (Faz 22E Step 5K)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from gbot.core.providers.openrouter_llm import OpenRouterLLM


# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def cfg():
    """Minimal config for provider tests."""
    return SimpleNamespace(
        assistant=SimpleNamespace(model="openrouter/moonshotai/kimi-k2.5"),
        providers=SimpleNamespace(
            openrouter=SimpleNamespace(api_key="sk-test-key", api_base=None),
            anthropic=SimpleNamespace(api_key="", api_base=None),
            openai=SimpleNamespace(api_key="sk-openai", api_base=None),
            deepseek=SimpleNamespace(api_key="", api_base=None),
            groq=SimpleNamespace(api_key="", api_base=None),
            gemini=SimpleNamespace(api_key="", api_base=None),
            moonshot=SimpleNamespace(api_key="", api_base=None),
        ),
    )


def _make_openrouter_response(content="hello", tool_calls=None, reasoning=None):
    """Build a mock OpenRouter SDK response."""
    msg = SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
        reasoning=reasoning,
        reasoning_content=None,
    )
    choice = SimpleNamespace(finish_reason="stop", message=msg)
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    return SimpleNamespace(choices=[choice], usage=usage)


# ── setup_provider ────────────────────────────────────────


def test_setup_provider_creates_openrouter(cfg):
    """setup_provider initialises OpenRouterLLM with the configured key."""
    from gbot.core.providers import litellm as facade

    facade.setup_provider(cfg)
    assert isinstance(facade._provider, OpenRouterLLM)


def test_setup_provider_falls_back_to_env_var(cfg, monkeypatch):
    """If config api_key is empty, OPENROUTER_API_KEY env var is used."""
    from gbot.core.providers import litellm as facade

    cfg.providers.openrouter.api_key = ""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-from-env")
    facade.setup_provider(cfg)
    assert isinstance(facade._provider, OpenRouterLLM)


def test_setup_provider_no_api_key_tolerant(cfg, monkeypatch):
    """Missing key leaves provider unset; achat() raises later, init doesn't."""
    from gbot.core.providers import litellm as facade

    cfg.providers.openrouter.api_key = ""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    facade.setup_provider(cfg)
    assert facade._provider is None


@pytest.mark.asyncio
async def test_achat_without_provider_raises(cfg, monkeypatch):
    """achat() without setup_provider (or with no key) surfaces a clear error."""
    from gbot.core.providers import litellm as facade

    cfg.providers.openrouter.api_key = ""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    facade.setup_provider(cfg)
    with pytest.raises(RuntimeError, match="OpenRouter provider not initialised"):
        await facade.achat(
            messages=[{"role": "user", "content": "hi"}],
            model="openrouter/test",
        )


# ── OpenRouterLLM._to_ai_message ─────────────────────────


def test_openrouter_to_ai_message_basic():
    """Basic content conversion."""
    response = _make_openrouter_response(content="Merhaba!")
    msg = OpenRouterLLM._to_ai_message(response)

    assert msg.content == "Merhaba!"
    assert msg.tool_calls == []
    assert msg.additional_kwargs == {}
    assert msg.response_metadata["usage"]["total_tokens"] == 15


def test_openrouter_to_ai_message_reasoning():
    """Reasoning content normalized to reasoning_content key."""
    response = _make_openrouter_response(
        content="answer", reasoning="I thought about it"
    )
    msg = OpenRouterLLM._to_ai_message(response)

    assert msg.additional_kwargs["reasoning_content"] == "I thought about it"


def test_openrouter_to_ai_message_tool_calls():
    """Tool calls parsed correctly."""
    tc = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(
            name="web_search",
            arguments='{"query": "weather istanbul"}',
        ),
    )
    response = _make_openrouter_response(content="", tool_calls=[tc])
    msg = OpenRouterLLM._to_ai_message(response)

    assert len(msg.tool_calls) == 1
    assert msg.tool_calls[0]["name"] == "web_search"
    assert msg.tool_calls[0]["args"] == {"query": "weather istanbul"}


def test_openrouter_to_ai_message_tool_calls_invalid_json():
    """Invalid tool call args fall back to raw."""
    tc = SimpleNamespace(
        id="call_2",
        function=SimpleNamespace(name="test", arguments="not json"),
    )
    response = _make_openrouter_response(content="", tool_calls=[tc])
    msg = OpenRouterLLM._to_ai_message(response)

    assert msg.tool_calls[0]["args"] == {"raw": "not json"}


def test_openrouter_to_ai_message_empty_content():
    """Empty content returns empty string."""
    response = _make_openrouter_response(content=None)
    response.choices[0].message.content = None
    msg = OpenRouterLLM._to_ai_message(response)

    assert msg.content == ""


# ── OpenRouterLLM.achat ──────────────────────────────────


@pytest.mark.asyncio
async def test_openrouter_achat_passes_response_format():
    """response_format is passed through to SDK (not stripped)."""
    schema = {"type": "json_schema", "json_schema": {"name": "test"}}
    provider = OpenRouterLLM(api_key="test")

    mock_response = _make_openrouter_response(content='{"key": "val"}')

    with patch.object(
        provider._client.chat, "send_async", new_callable=AsyncMock,
        return_value=mock_response,
    ) as mock_send:
        await provider.achat(
            messages=[{"role": "user", "content": "test"}],
            model="openrouter/moonshotai/kimi-k2.5",
            response_format=schema,
        )
        call_kwargs = mock_send.call_args
        assert call_kwargs.kwargs.get("response_format") == schema
        # Verify model prefix stripped
        assert call_kwargs.kwargs.get("model") == "moonshotai/kimi-k2.5"


@pytest.mark.asyncio
async def test_openrouter_achat_thinking_mode():
    """Thinking mode sets reasoning parameter."""
    provider = OpenRouterLLM(api_key="test")
    mock_response = _make_openrouter_response(content="thought")

    with patch.object(
        provider._client.chat, "send_async", new_callable=AsyncMock,
        return_value=mock_response,
    ) as mock_send:
        await provider.achat(
            messages=[{"role": "user", "content": "think"}],
            model="openrouter/moonshotai/kimi-k2.5",
            thinking=True,
        )
        call_kwargs = mock_send.call_args.kwargs
        assert call_kwargs["reasoning"] == {"effort": "medium"}
        assert call_kwargs["temperature"] == 1.0


@pytest.mark.asyncio
async def test_openrouter_achat_error_handling():
    """Errors return AIMessage with error content."""
    provider = OpenRouterLLM(api_key="test")

    with patch.object(
        provider._client.chat, "send_async", new_callable=AsyncMock,
        side_effect=Exception("connection failed"),
    ):
        result = await provider.achat(
            messages=[{"role": "user", "content": "test"}],
            model="openrouter/test",
        )
        assert "Error calling LLM" in result.content


# ── Facade routing ────────────────────────────────────────


@pytest.mark.asyncio
async def test_facade_forwards_to_provider(cfg):
    """facade.achat delegates to the OpenRouter provider."""
    from gbot.core.providers import litellm as facade

    facade.setup_provider(cfg)

    mock_response = _make_openrouter_response(content="routed!")
    with patch.object(
        facade._provider._client.chat, "send_async",
        new_callable=AsyncMock, return_value=mock_response,
    ):
        result = await facade.achat(
            messages=[{"role": "user", "content": "test"}],
            model="openrouter/moonshotai/kimi-k2.5",
        )
        assert result.content == "routed!"


@pytest.mark.asyncio
async def test_facade_works_with_non_openrouter_prefix(cfg):
    """Even openai/* model strings get forwarded — OpenRouter routes them upstream."""
    from gbot.core.providers import litellm as facade

    facade.setup_provider(cfg)

    mock_response = _make_openrouter_response(content="ok")
    with patch.object(
        facade._provider._client.chat, "send_async",
        new_callable=AsyncMock, return_value=mock_response,
    ):
        result = await facade.achat(
            messages=[{"role": "user", "content": "test"}],
            model="openai/gpt-4o-mini",
        )
        assert result.content == "ok"
