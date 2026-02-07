"""Tests for RAG — SemanticRetriever + search tool integration."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

faiss = pytest.importorskip("faiss", reason="faiss-cpu not installed")

import numpy as np

from graphbot.core.config.schema import RagConfig
from graphbot.rag.retriever import SemanticRetriever

EMBED_DIM = 32

# ── Test data ───────────────────────────────────────────────

SAMPLE_ITEMS = [
    {"id": "1", "title": "Python Basics", "description": "Introduction to Python programming"},
    {"id": "2", "title": "Machine Learning", "description": "ML fundamentals and algorithms"},
    {"id": "3", "title": "Web Development", "description": "Building web apps with FastAPI"},
    {"id": "4", "title": "Data Science", "description": "Data analysis and visualization"},
    {"id": "5", "title": "Deep Learning", "description": "Neural networks and deep learning"},
]


# ── Helpers ─────────────────────────────────────────────────


class FakeEmbedder:
    """Mock SentenceTransformer that returns deterministic embeddings."""

    def encode(self, texts, **kwargs):
        rng = np.random.RandomState(42)
        embeddings = rng.randn(len(texts), EMBED_DIM).astype("float32")
        # Normalize
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        return embeddings / norms


def _make_retriever(tmp_path: Path, items: list | None = None) -> SemanticRetriever:
    """Create a retriever with mock embedder and test data."""
    data_file = tmp_path / "items.json"
    index_dir = tmp_path / "faiss_index"

    if items is not None:
        data_file.write_text(json.dumps(items), encoding="utf-8")

    config = RagConfig(
        embedding_model="fake-model",
        data_source=str(data_file),
        index_path=str(index_dir),
        text_template="{title}. {description}.",
        id_field="id",
    )

    # Patch SentenceTransformer to avoid downloading a real model
    import graphbot.rag.retriever as mod

    original_st = mod.SentenceTransformer
    mod.SentenceTransformer = lambda *a, **kw: FakeEmbedder()
    try:
        retriever = SemanticRetriever(config)
    finally:
        mod.SentenceTransformer = original_st

    return retriever


# ── Tests ───────────────────────────────────────────────────


def test_retriever_loads_items(tmp_path):
    retriever = _make_retriever(tmp_path, SAMPLE_ITEMS)
    assert retriever.count == 5
    assert retriever.ready is True


def test_retriever_empty_data(tmp_path):
    retriever = _make_retriever(tmp_path, items=None)  # no data file
    assert retriever.count == 0
    assert retriever.ready is False


def test_search_returns_results(tmp_path):
    retriever = _make_retriever(tmp_path, SAMPLE_ITEMS)
    results = retriever.search("python programming")
    assert len(results) > 0
    item, score = results[0]
    assert isinstance(item, dict)
    assert isinstance(score, float)


def test_search_exclude_ids(tmp_path):
    retriever = _make_retriever(tmp_path, SAMPLE_ITEMS)
    results = retriever.search("programming", exclude_ids=["1", "2", "3", "4", "5"])
    assert len(results) == 0


def test_get_by_id_found(tmp_path):
    retriever = _make_retriever(tmp_path, SAMPLE_ITEMS)
    item = retriever.get_by_id("3")
    assert item is not None
    assert item["title"] == "Web Development"


def test_get_by_id_not_found(tmp_path):
    retriever = _make_retriever(tmp_path, SAMPLE_ITEMS)
    item = retriever.get_by_id("999")
    assert item is None


def test_format_results(tmp_path):
    retriever = _make_retriever(tmp_path, SAMPLE_ITEMS)
    results = retriever.search("python", top_k=2)
    text = retriever.format_results(results)
    assert "1." in text
    assert "ID:" in text


def test_format_results_empty(tmp_path):
    retriever = _make_retriever(tmp_path, SAMPLE_ITEMS)
    text = retriever.format_results([])
    assert "No items found" in text


def test_search_tools_with_retriever(tmp_path):
    from graphbot.agent.tools.search import make_search_tools

    retriever = _make_retriever(tmp_path, SAMPLE_ITEMS)
    tools = make_search_tools(retriever)
    search = next(t for t in tools if t.name == "search_items")
    result = search.invoke({"query": "python"})
    assert "mock" not in result.lower()


def test_search_tools_without_retriever():
    from graphbot.agent.tools.search import make_search_tools

    tools = make_search_tools(retriever=None)
    search = next(t for t in tools if t.name == "search_items")
    result = search.invoke({"query": "test"})
    assert "mock" in result.lower()
