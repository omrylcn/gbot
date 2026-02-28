"""Tests for LLM provider strategy pattern."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from graphbot.core.providers.litellm_llm import LiteLLMLLM
from graphbot.core.providers.openrouter_llm import OpenRouterLLM


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


# ── Factory routing ───────────────────────────────────────


def test_setup_provider_openrouter(cfg):
    """openrouter/ model creates OpenRouterLLM as main provider."""
    from graphbot.core.providers import litellm as facade

    facade.setup_provider(cfg)
    assert isinstance(facade._main_provider, OpenRouterLLM)
    assert isinstance(facade._fallback_provider, LiteLLMLLM)


def test_setup_provider_non_openrouter(cfg):
    """Non-openrouter model creates LiteLLMLLM as main provider."""
    from graphbot.core.providers import litellm as facade

    cfg.assistant.model = "openai/gpt-4o"
    facade.setup_provider(cfg)
    assert isinstance(facade._main_provider, LiteLLMLLM)
    assert isinstance(facade._fallback_provider, LiteLLMLLM)


def test_setup_provider_no_api_key(cfg, monkeypatch):
    """Missing OpenRouter API key falls back to LiteLLM."""
    from graphbot.core.providers import litellm as facade

    cfg.providers.openrouter.api_key = ""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    facade.setup_provider(cfg)
    assert isinstance(facade._main_provider, LiteLLMLLM)


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
        # Verify response_format was passed
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
async def test_facade_routes_openrouter_to_main(cfg):
    """Facade routes openrouter/* models to OpenRouterLLM."""
    from graphbot.core.providers import litellm as facade

    facade.setup_provider(cfg)

    mock_response = _make_openrouter_response(content="routed!")
    with patch.object(
        facade._main_provider._client.chat, "send_async",
        new_callable=AsyncMock, return_value=mock_response,
    ):
        result = await facade.achat(
            messages=[{"role": "user", "content": "test"}],
            model="openrouter/moonshotai/kimi-k2.5",
        )
        assert result.content == "routed!"


@pytest.mark.asyncio
async def test_facade_routes_openai_to_fallback(cfg):
    """Facade routes non-openrouter models to LiteLLMLLM."""
    from graphbot.core.providers import litellm as facade

    facade.setup_provider(cfg)

    with patch("graphbot.core.providers.litellm_llm.litellm.acompletion",
               new_callable=AsyncMock) as mock_llm:
        # Build a litellm-style response
        msg = MagicMock()
        msg.content = "from litellm"
        msg.tool_calls = None
        msg.reasoning_content = None
        choice = MagicMock()
        choice.message = msg
        choice.finish_reason = "stop"
        usage = MagicMock()
        usage.prompt_tokens = 5
        usage.completion_tokens = 3
        usage.total_tokens = 8
        mock_llm.return_value = MagicMock(choices=[choice], usage=usage)

        result = await facade.achat(
            messages=[{"role": "user", "content": "test"}],
            model="openai/gpt-4o-mini",
        )
        assert result.content == "from litellm"
        mock_llm.assert_called_once()
