"""LLM eval fixtures — model parametrization, API-key gating, fixture loaders.

Usage:
    # Default model (config.memory.model or config.assistant.model)
    uv run pytest tests/llm_eval/

    # Test a specific model (e.g., Kimi 2.6 just released)
    uv run pytest tests/llm_eval/ --model=openrouter/moonshotai/kimi-k2.6

    # A/B compare: run twice with different --model
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from gbot.core.config.loader import load_config
from gbot.core.providers.litellm import setup_provider

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ── CLI option ──────────────────────────────────────────────────


def pytest_addoption(parser):
    parser.addoption(
        "--model",
        action="store",
        default=None,
        help=(
            "LLM to evaluate (e.g. 'openrouter/anthropic/claude-haiku-4.5'). "
            "If unset, uses config.memory.model or config.assistant.model."
        ),
    )


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def llm_model(pytestconfig) -> str:
    """The model under test. CLI flag overrides config default."""
    explicit = pytestconfig.getoption("--model")
    if explicit:
        return explicit
    cfg = load_config()
    return cfg.memory.model or cfg.assistant.model


@pytest.fixture(scope="session", autouse=True)
def _require_api_key():
    """Skip the entire suite if no LLM provider key is configured.

    All three eval suites make real LLM calls — without a key the test
    would either crash or return zero/error responses that don't reflect
    quality.
    """
    has_key = any(
        os.environ.get(k)
        for k in (
            "OPENROUTER_API_KEY",
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
        )
    )
    if not has_key:
        pytest.skip(
            "LLM eval requires an API key (OPENROUTER_API_KEY etc.) — skipping",
            allow_module_level=True,
        )


@pytest.fixture(autouse=True)
def _setup_llm_provider(_require_api_key):
    """Re-initialise the LLM provider before each test.

    Function scope is intentional: the OpenRouter SDK lazily creates an
    httpx async client on the first call and binds it to the running
    event loop. pytest-asyncio (auto mode) opens a fresh loop per test,
    so re-running ``setup_provider`` ensures the global provider's next
    httpx client is bound to the *current* loop.
    """
    cfg = load_config()
    setup_provider(cfg)


@pytest.fixture(scope="session")
def extraction_cases() -> list[dict]:
    with open(FIXTURES_DIR / "extraction_cases.json") as f:
        return json.load(f)["cases"]


@pytest.fixture(scope="session")
def audn_cases() -> list[dict]:
    with open(FIXTURES_DIR / "audn_cases.json") as f:
        return json.load(f)["cases"]


@pytest.fixture(scope="session")
def page_cases() -> list[dict]:
    with open(FIXTURES_DIR / "page_cases.json") as f:
        return json.load(f)["cases"]
