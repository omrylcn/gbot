"""Tests for Faz 22B — Memory Layer: facts, embedder, extraction, AUDN."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gbot.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(str(tmp_path / "test.db"))
    s.get_or_create_user("u1", "Test User")
    return s


# ── memory_facts CRUD ────────────────────────────────────


def test_add_and_get_fact(store):
    store.add_fact("f1", "u1", "Lives in Istanbul", fact_type="semantic")
    facts = store.get_facts("u1")
    assert len(facts) == 1
    assert facts[0]["content"] == "Lives in Istanbul"
    assert facts[0]["fact_type"] == "semantic"


def test_add_fact_with_embedding(store):
    emb = [0.1] * 3072
    store.add_fact("f1", "u1", "Uses Python", embedding=emb)
    fact = store.get_fact("f1")
    assert fact is not None
    assert fact["content"] == "Uses Python"


def test_invalidate_fact(store):
    store.add_fact("f1", "u1", "Lives in Istanbul")
    store.add_fact("f2", "u1", "Lives in Ankara")
    store.invalidate_fact("f1", superseded_by="f2")

    active = store.get_facts("u1", valid_only=True)
    assert len(active) == 1
    assert active[0]["fact_id"] == "f2"

    all_facts = store.get_facts("u1", valid_only=False)
    assert len(all_facts) == 2


def test_invalidate_removes_vec(store):
    """Invalidating a fact also removes its vector."""
    emb = [0.1] * 3072
    store.add_fact("f1", "u1", "test fact", embedding=emb)

    # Verify vec exists
    with store._get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM vec_memory_facts").fetchone()[0]
    assert count == 1

    store.invalidate_fact("f1")

    with store._get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM vec_memory_facts").fetchone()[0]
    assert count == 0


def test_search_similar_empty(store):
    """Search on empty table returns empty list."""
    results = store.search_similar_facts("u1", [0.1] * 3072)
    assert results == []


def test_search_similar_facts(store):
    """Search returns facts ordered by distance."""
    emb1 = [1.0] + [0.0] * 3071
    emb2 = [0.9, 0.1] + [0.0] * 3070
    emb3 = [0.0] * 3071 + [1.0]

    store.add_fact("f1", "u1", "close fact", embedding=emb1)
    store.add_fact("f2", "u1", "medium fact", embedding=emb2)
    store.add_fact("f3", "u1", "far fact", embedding=emb3)

    query = [1.0] + [0.0] * 3071
    results = store.search_similar_facts("u1", query, top_k=2)
    assert len(results) == 2
    assert results[0]["fact_id"] == "f1"  # closest


def test_search_filters_invalid(store):
    """Search excludes invalidated facts."""
    emb = [1.0] + [0.0] * 3071
    store.add_fact("f1", "u1", "valid fact", embedding=emb)
    store.add_fact("f2", "u1", "invalid fact", embedding=emb)
    store.invalidate_fact("f2")

    results = store.search_similar_facts("u1", emb, top_k=5)
    assert len(results) == 1
    assert results[0]["fact_id"] == "f1"


def test_search_filters_user(store):
    """Search only returns facts for the specified user."""
    emb = [1.0] + [0.0] * 3071
    store.get_or_create_user("u2")
    store.add_fact("f1", "u1", "user1 fact", embedding=emb)
    store.add_fact("f2", "u2", "user2 fact", embedding=emb)

    results = store.search_similar_facts("u1", emb, top_k=5)
    assert len(results) == 1
    assert results[0]["fact_id"] == "f1"


def test_fact_stats(store):
    store.add_fact("f1", "u1", "fact1", fact_type="semantic")
    store.add_fact("f2", "u1", "fact2", fact_type="preference")
    store.add_fact("f3", "u1", "fact3", fact_type="semantic")

    stats = store.get_fact_stats("u1")
    assert stats["total"] == 3
    assert stats["by_type"]["semantic"]["count"] == 2
    assert stats["by_type"]["preference"]["count"] == 1


# ── Temporal user_notes ──────────────────────────────────


def test_notes_with_ids(store):
    store.add_note("u1", "note1")
    store.add_note("u1", "note2")
    notes = store.get_notes_with_ids("u1")
    assert len(notes) == 2
    assert "id" in notes[0]
    assert "content" in notes[0]


def test_delete_note(store):
    store.add_note("u1", "to delete")
    notes = store.get_notes_with_ids("u1")
    store.delete_note(notes[0]["id"])
    assert len(store.get_notes("u1")) == 0


# ── Processing log ───────────────────────────────────────


def test_processing_log(store):
    log_id = store.log_memory_processing(
        "u1", trigger="test", facts_extracted=5, facts_added=3,
        facts_updated=1, facts_invalidated=1, duration_ms=100,
    )
    assert log_id > 0
    log = store.get_processing_log("u1")
    assert len(log) == 1
    assert log[0]["trigger"] == "test"
    assert log[0]["facts_added"] == 3


# ── MemoryEmbedder ───────────────────────────────────────


def test_embedder_no_key():
    """Embedder without API key returns zero vectors."""
    from gbot.memory.embedder import MemoryEmbedder
    from gbot.core.config.schema import MemoryEmbeddingConfig

    cfg = MemoryEmbeddingConfig(dimension=8)
    embedder = MemoryEmbedder(cfg, api_key="")
    result = embedder.embed("test")
    assert len(result) == 8
    assert all(v == 0.0 for v in result)


# ── MemoryService extraction (mocked LLM) ────────────────


@pytest.mark.asyncio
async def test_extract_and_save_basic(store):
    """MemoryService extracts facts from conversation."""
    from gbot.memory.extraction import MemoryService
    from langchain_core.messages import AIMessage

    mock_response = AIMessage(content=json.dumps({
        "facts": [
            {"content": "Lives in Istanbul", "type": "semantic", "confidence": 1.0,
             "category": "location", "keywords": ["istanbul"]},
        ]
    }))

    with patch("gbot.core.providers.litellm.achat", return_value=mock_response):
        with patch("gbot.core.providers.litellm.setup_provider"):
            mem = MemoryService(store)
            stats = await mem.extract_and_save("u1", [
                {"role": "user", "content": "I live in Istanbul"},
            ])

    assert stats["facts_extracted"] == 1
    assert stats["facts_added"] == 1
    facts = store.get_facts("u1")
    assert len(facts) == 1
    assert "Istanbul" in facts[0]["content"]


@pytest.mark.asyncio
async def test_extract_empty(store):
    """Empty extraction doesn't error."""
    from gbot.memory.extraction import MemoryService
    from langchain_core.messages import AIMessage

    mock_response = AIMessage(content='{"facts": []}')

    with patch("gbot.core.providers.litellm.achat", return_value=mock_response):
        with patch("gbot.core.providers.litellm.setup_provider"):
            mem = MemoryService(store)
            stats = await mem.extract_and_save("u1", [
                {"role": "user", "content": "hello"},
            ])

    assert stats["facts_extracted"] == 0
    assert stats["facts_added"] == 0
