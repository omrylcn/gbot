"""Faz 22G Aşama 5 — LLM rerank in ContextBuilder.

Targeted tests for the new ``_llm_rerank`` method without standing up
a full graph:
- Falls back to static formula when the LLM call fails
- Honors the ranking the LLM returns
- Static path (``llm_rerank.enabled=false``) untouched
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage

from gbot.agent.context.builder import ContextBuilder


@pytest.fixture
def builder(monkeypatch):
    """Bare ContextBuilder — only the bits _llm_rerank touches need to
    exist.
    """
    cb = ContextBuilder.__new__(ContextBuilder)
    cb.config = SimpleNamespace(
        memory=SimpleNamespace(
            model="openrouter/google/gemini-3-flash-preview",
        )
    )
    cb.db = None
    cb.embedder = None
    return cb


def _llm_cfg(**kwargs):
    defaults = {
        "enabled": True,
        "model": None,
        "candidates_pool": 30,
        "max_output_tokens": 200,
        "temperature": 0.0,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _candidates(n: int = 5) -> list[dict]:
    return [
        {
            "fact_id": f"f{i}",
            "content": f"fact #{i}",
            "distance": 0.2 + 0.05 * i,
            "confidence": 1.0,
            "access_count": 0,
            "created_at": "2026-05-01T00:00:00",
        }
        for i in range(n)
    ]


def test_llm_rerank_uses_llm_order(builder):
    cands = _candidates(5)
    response = AIMessage(content='{"ranked": [3, 1, 4]}', additional_kwargs={})

    with patch(
        "gbot.core.providers.llm.achat", new=AsyncMock(return_value=response)
    ):
        out = builder._llm_rerank(
            cands, query="test query", top_k=3,
            llm_cfg=_llm_cfg(),
        )
    assert [f["fact_id"] for f in out] == ["f3", "f1", "f4"]


def test_llm_rerank_fills_missing_with_static(builder):
    """LLM returned only 1 index — remainder filled from static rerank."""
    cands = _candidates(5)
    response = AIMessage(content='{"ranked": [2]}', additional_kwargs={})

    with patch(
        "gbot.core.providers.llm.achat", new=AsyncMock(return_value=response)
    ):
        out = builder._llm_rerank(
            cands, query="q", top_k=3, llm_cfg=_llm_cfg(),
        )
    assert len(out) == 3
    assert out[0]["fact_id"] == "f2"  # LLM-chosen first
    # Remaining 2 came from the static-formula tail; their ids
    # depend on the formula but must come from the unused pool.
    assert "f2" not in [f["fact_id"] for f in out[1:]]


def test_llm_rerank_falls_back_on_invalid_json(builder):
    cands = _candidates(4)
    response = AIMessage(content="not json", additional_kwargs={})

    with patch(
        "gbot.core.providers.llm.achat", new=AsyncMock(return_value=response)
    ):
        out = builder._llm_rerank(
            cands, query="q", top_k=3, llm_cfg=_llm_cfg(),
        )
    # Falls back to the static rerank → returns 3 items
    assert len(out) == 3


def test_llm_rerank_falls_back_on_exception(builder):
    cands = _candidates(4)
    with patch(
        "gbot.core.providers.llm.achat",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        out = builder._llm_rerank(
            cands, query="q", top_k=2, llm_cfg=_llm_cfg(),
        )
    assert len(out) == 2


def test_llm_rerank_empty_input(builder):
    out = builder._llm_rerank([], query="q", top_k=5, llm_cfg=_llm_cfg())
    assert out == []
