"""Tests for memory_entity_pages CRUD + compiler stale-on-invalidate hook
(Faz 22D Step 6/7).

The async compiler with LLM call is exercised at the unit-isolation level
with the LLM mocked. The end-to-end live compile is covered by manual
verification (documented in notes/test.md).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage

from gbot.core.config.schema import MemoryConfig, MemoryEntityPagesConfig
from gbot.memory.entities import EntityResolver
from gbot.memory.entity_pages import EntityPageCompiler
from gbot.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    db = MemoryStore(str(tmp_path / "pages.db"))
    db.get_or_create_user("u1", "Test")
    return db


@pytest.fixture
def memory_cfg_enabled():
    return MemoryConfig(
        entity_pages=MemoryEntityPagesConfig(
            enabled=True,
            min_facts_for_page=1,    # easier to trigger in tests
            min_relations_for_page=1,
            debounce_seconds=1,
        ),
    )


@pytest.fixture
def memory_cfg_disabled():
    return MemoryConfig(entity_pages=MemoryEntityPagesConfig(enabled=False))


# ── Store-level tests (no LLM) ───────────────────────────────────────


def test_upsert_creates_new_page(store):
    page_id = store.upsert_entity_page(
        user_id="u1",
        entity_canonical="Murat",
        content_md="Murat is a colleague.",
        source_fact_ids=["f1", "f2"],
        source_relation_ids=["r1"],
    )
    assert page_id
    page = store.get_entity_page("u1", "Murat")
    assert page is not None
    assert page["content_md"] == "Murat is a colleague."
    assert page["fact_count"] == 2
    assert page["version"] == 1
    assert page["stale"] == 0


def test_upsert_updates_existing_page(store):
    pid1 = store.upsert_entity_page("u1", "Murat", "v1 text", source_fact_ids=["f1"])
    pid2 = store.upsert_entity_page("u1", "Murat", "v2 text", source_fact_ids=["f1", "f2"])
    assert pid1 == pid2  # same page_id reused
    page = store.get_entity_page("u1", "Murat")
    assert page["content_md"] == "v2 text"
    assert page["version"] == 2
    assert page["fact_count"] == 2


def test_get_entity_page_increments_access(store):
    store.upsert_entity_page("u1", "Murat", "x", source_fact_ids=["f1"])
    a = store.get_entity_page("u1", "Murat")
    b = store.get_entity_page("u1", "Murat")
    assert b["access_count"] == a["access_count"] + 1


def test_invalidate_fact_marks_dependent_pages_stale(store):
    """When a fact is invalidated, every page that cites it goes stale."""
    store.add_fact(
        fact_id="f-stale-test",
        user_id="u1",
        content="Murat works at Acme",
        fact_type="semantic",
        category="work",
    )
    store.upsert_entity_page(
        "u1", "Murat", "Murat works at Acme.", source_fact_ids=["f-stale-test"]
    )
    page = store.get_entity_page("u1", "Murat")
    assert page["stale"] == 0

    store.invalidate_fact("f-stale-test")
    page = store.get_entity_page("u1", "Murat")
    assert page["stale"] == 1


def test_invalidate_unrelated_fact_does_not_mark_stale(store):
    store.add_fact(
        fact_id="f-unrelated",
        user_id="u1",
        content="something else",
        fact_type="semantic",
        category="personal",
    )
    store.upsert_entity_page(
        "u1", "Murat", "Murat is a colleague.", source_fact_ids=["f-some-other"]
    )

    store.invalidate_fact("f-unrelated")
    page = store.get_entity_page("u1", "Murat")
    assert page["stale"] == 0


def test_mark_pages_stale_by_fact_pattern(store):
    """JSON-LIKE pattern matches the fact_id inside source_fact_ids JSON."""
    store.upsert_entity_page("u1", "A", "ax", source_fact_ids=["f-AAA", "f-BBB"])
    store.upsert_entity_page("u1", "B", "bx", source_fact_ids=["f-CCC"])
    store.upsert_entity_page("u1", "C", "cx", source_fact_ids=[])

    n = store.mark_pages_stale_by_fact("u1", "f-AAA")
    assert n == 1
    assert store.get_entity_page("u1", "A")["stale"] == 1
    assert store.get_entity_page("u1", "B")["stale"] == 0
    assert store.get_entity_page("u1", "C")["stale"] == 0


def test_delete_entity_page(store):
    store.upsert_entity_page("u1", "X", "xx", source_fact_ids=["f1"])
    assert store.delete_entity_page("u1", "X") is True
    assert store.get_entity_page("u1", "X") is None
    # Idempotent — deleting again returns False
    assert store.delete_entity_page("u1", "X") is False


def test_forget_entity_cascade_archive(store):
    """forget_entity invalidates relations + facts + deletes the page."""
    # Add a relation involving Murat
    store.add_relation(
        "rm-1", "u1", "Murat", "works_with", "Test",
        canonical_source="Murat", canonical_target="u1",
    )
    # Add a fact mentioning Murat
    store.add_fact(
        fact_id="f-fe-1",
        user_id="u1",
        content="Murat is a backend developer.",
        fact_type="semantic",
        category="work",
    )
    # And a page for Murat
    store.upsert_entity_page("u1", "Murat", "summary", source_fact_ids=["f-fe-1"])

    result = store.forget_entity("u1", "Murat")
    assert result["relations"] == 1
    assert result["facts"] == 1
    assert result["pages"] == 1

    # Verify cascading
    assert store.get_entity_page("u1", "Murat") is None
    rels = store.get_relations("u1", canonical="Murat")
    assert len(rels) == 0  # all valid relations archived
    valid_facts = store.get_facts("u1", valid_only=True)
    assert all("Murat" not in f["content"] for f in valid_facts)


def test_forget_entity_does_not_touch_unrelated_facts(store):
    store.add_fact(
        fact_id="f-keep",
        user_id="u1",
        content="Ali likes coffee.",
        fact_type="preference",
        category="preference",
    )
    store.add_fact(
        fact_id="f-drop",
        user_id="u1",
        content="Murat works at Acme.",
        fact_type="semantic",
        category="work",
    )

    store.forget_entity("u1", "Murat")

    valid = {f["fact_id"] for f in store.get_facts("u1", valid_only=True)}
    assert "f-keep" in valid
    assert "f-drop" not in valid


def test_list_entity_pages_orders_by_access(store):
    store.upsert_entity_page("u1", "A", "a")
    store.upsert_entity_page("u1", "B", "b")
    # Access B twice, A once
    store.get_entity_page("u1", "B")
    store.get_entity_page("u1", "B")
    store.get_entity_page("u1", "A")
    pages = store.list_entity_pages("u1")
    assert pages[0]["entity_canonical"] == "B"
    assert pages[1]["entity_canonical"] == "A"


# ── Compiler tests (LLM mocked) ──────────────────────────────────────


@pytest.mark.asyncio
async def test_compiler_disabled_when_flag_off(store, memory_cfg_disabled):
    resolver = EntityResolver(store, owner_username="u1")
    compiler = EntityPageCompiler(store, memory_cfg_disabled, resolver=resolver)
    assert compiler.enabled is False
    # enqueue should be a no-op
    await compiler.enqueue("u1", "Murat")
    assert store.get_entity_page("u1", "Murat") is None


@pytest.mark.asyncio
async def test_compiler_skips_when_eligibility_unmet(store, memory_cfg_enabled):
    """min_facts_for_page=1 — but if the entity has 0 facts AND 0 relations, skip."""
    cfg = MemoryConfig(
        entity_pages=MemoryEntityPagesConfig(
            enabled=True,
            min_facts_for_page=2,
            min_relations_for_page=2,
        )
    )
    resolver = EntityResolver(store, owner_username="u1")
    compiler = EntityPageCompiler(store, cfg, resolver=resolver)
    result = await compiler.compile_now("u1", "GhostEntity")
    assert result is None


@pytest.mark.asyncio
async def test_compiler_persists_page_with_provenance(store, memory_cfg_enabled):
    """End-to-end: facts present → LLM returns markdown → page stored
    with source_fact_ids set."""
    store.add_fact(
        fact_id="f-mu-1",
        user_id="u1",
        content="Murat is a backend developer.",
        fact_type="semantic",
        category="work",
    )
    store.add_fact(
        fact_id="f-mu-2",
        user_id="u1",
        content="Murat uses Python.",
        fact_type="semantic",
        category="tech",
    )

    resolver = EntityResolver(store, owner_username="u1")
    compiler = EntityPageCompiler(store, memory_cfg_enabled, resolver=resolver)

    fake_response = AIMessage(content="Murat is a backend developer who uses Python.")
    with patch(
        "gbot.memory.entity_pages.llm_provider.achat",
        new_callable=AsyncMock,
        return_value=fake_response,
    ):
        page_dict = await compiler.compile_now("u1", "Murat")

    assert page_dict is not None
    page = store.get_entity_page("u1", "Murat")
    assert "Murat" in page["content_md"]
    fact_ids = page["source_fact_ids"]
    assert "f-mu-1" in fact_ids
    assert "f-mu-2" in fact_ids
    assert page["stale"] == 0


@pytest.mark.asyncio
async def test_enqueue_marks_stale_immediately(store, memory_cfg_enabled):
    """enqueue() should set stale=1 right away — read path uses this for
    fallback decisions even before debounce fires.
    """
    store.upsert_entity_page("u1", "Murat", "old content", source_fact_ids=["f1"])
    assert store.get_entity_page("u1", "Murat")["stale"] == 0

    resolver = EntityResolver(store, owner_username="u1")
    compiler = EntityPageCompiler(store, memory_cfg_enabled, resolver=resolver)

    # Patch achat so the eventual compile doesn't actually call an API
    with patch(
        "gbot.memory.entity_pages.llm_provider.achat",
        new_callable=AsyncMock,
        return_value=AIMessage(content="new content"),
    ):
        await compiler.enqueue("u1", "Murat")
        page = store.get_entity_page("u1", "Murat")
        assert page["stale"] == 1
        # Cancel the pending task to keep the test fast — debounce_seconds=1
        async with compiler._lock:
            for task in compiler._pending.values():
                if task.handle:
                    task.handle.cancel()
