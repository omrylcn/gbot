"""Faz 22G — 4-state lifecycle on memory_facts.

Covers schema migration (state column + backfill), decay state
transitions, retrieval filter, and inhibit/restore helpers.
"""

from __future__ import annotations

import pytest

from gbot.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(str(tmp_path / "lifecycle.db"))


# ── Schema + migration ──────────────────────────────────────────


def test_schema_has_state_column(store):
    with store._get_conn() as conn:
        cols = {
            r[1]
            for r in conn.execute(
                "PRAGMA table_info(memory_facts)"
            ).fetchall()
        }
    assert "state" in cols
    assert "inhibited_until" in cols
    assert "last_accessed_at" in cols


def test_user_version_is_at_least_22g(tmp_path):
    """Schema migrations move user_version forward over time. Faz 22G
    bumped to 23, Faz 22J to 24. The lifecycle columns were added in
    22G, so anything ≥ 23 means the migration this file cares about
    has run.
    """
    db = MemoryStore(str(tmp_path / "v.db"))
    with db._get_conn() as conn:
        v = conn.execute("PRAGMA user_version").fetchone()[0]
    assert v >= 23


def test_backfill_sets_state_from_implicit_signals(tmp_path):
    """Pre-22G data: low-importance row → 'weak'; valid_until set → 'archived'."""
    path = str(tmp_path / "pre_22g.db")
    # Build a fresh DB then mutate to simulate pre-22G state where some
    # rows have valid_until set or low importance.
    store = MemoryStore(path)
    store.get_or_create_user("u1", "u1")
    store.add_fact(
        fact_id="f_active", user_id="u1", content="taze", importance=0.8
    )
    store.add_fact(
        fact_id="f_weak", user_id="u1", content="zayıf", importance=0.2
    )
    store.add_fact(
        fact_id="f_arch", user_id="u1", content="arşiv"
    )
    store.invalidate_fact("f_arch")

    # Reset state to default + valid_until/importance preserved, then
    # re-run the backfill UPDATE to verify the migration logic itself.
    with store._get_conn() as conn:
        conn.execute("UPDATE memory_facts SET state = 'active'")
        conn.commit()
        conn.execute(
            """UPDATE memory_facts SET state = CASE
                   WHEN valid_until IS NOT NULL THEN 'archived'
                   WHEN importance < 0.3 THEN 'weak'
                   ELSE 'active'
               END
               WHERE state = 'active'"""
        )
        conn.commit()
        rows = {
            r["fact_id"]: r["state"]
            for r in conn.execute(
                "SELECT fact_id, state FROM memory_facts"
            ).fetchall()
        }
    assert rows["f_active"] == "active"
    assert rows["f_weak"] == "weak"
    assert rows["f_arch"] == "archived"


# ── inhibit / restore ──────────────────────────────────────────


def test_inhibit_excludes_from_retrieval(store):
    store.get_or_create_user("u1", "u1")
    embedding = [0.1] * 3072
    store.add_fact(
        fact_id="f1", user_id="u1", content="gizli bilgi",
        embedding=embedding,
    )
    # Search hits f1
    hits_before = store.search_similar_facts("u1", embedding, top_k=5)
    assert any(h["fact_id"] == "f1" for h in hits_before)

    # Inhibit → retrieval excludes
    assert store.inhibit_fact("f1", hold_days=7) is True
    hits_after = store.search_similar_facts("u1", embedding, top_k=5)
    assert not any(h["fact_id"] == "f1" for h in hits_after)

    # Restore → retrieval picks it up again
    assert store.restore_fact("f1") is True
    hits_restored = store.search_similar_facts("u1", embedding, top_k=5)
    assert any(h["fact_id"] == "f1" for h in hits_restored)


def test_inhibit_returns_false_for_missing_fact(store):
    assert store.inhibit_fact("nonexistent") is False


def test_restore_returns_false_for_active_fact(store):
    store.get_or_create_user("u1", "u1")
    store.add_fact(fact_id="active1", user_id="u1", content="taze")
    # Already ACTIVE — restore is a no-op
    assert store.restore_fact("active1") is False


# ── decay state transitions ────────────────────────────────────


def test_decay_promotes_active_to_weak(store):
    """Old, untouched ACTIVE row → WEAK after stage-1 fade."""
    store.get_or_create_user("u1", "u1")
    store.add_fact(
        fact_id="old1", user_id="u1", content="eski episodic",
        fact_type="episodic", importance=0.6,
    )
    # Force created_at into the past so fade_days threshold trips.
    with store._get_conn() as conn:
        conn.execute(
            "UPDATE memory_facts SET created_at = datetime('now', '-30 days')"
        )
        conn.commit()
    result = store.apply_decay("u1")
    assert result["faded"] >= 1
    fact = store.get_fact("old1")
    assert fact["state"] == "weak"


def test_decay_archives_low_importance(store):
    """Once importance falls below threshold, decay sets ARCHIVED."""
    store.get_or_create_user("u1", "u1")
    store.add_fact(
        fact_id="dim1", user_id="u1", content="kaybolmaya yakın",
        importance=0.05,
    )
    result = store.apply_decay("u1", archive_threshold=0.1)
    assert result["archived"] >= 1
    fact = store.get_fact("dim1")
    assert fact["state"] == "archived"
    assert fact["valid_until"] is not None


def test_decay_auto_restores_inhibited_after_hold(store):
    store.get_or_create_user("u1", "u1")
    store.add_fact(fact_id="hold1", user_id="u1", content="geri gelir")
    # Inhibit and force inhibited_until into the past so apply_decay
    # picks it up as expired.
    store.inhibit_fact("hold1", hold_days=7)
    with store._get_conn() as conn:
        conn.execute(
            "UPDATE memory_facts SET inhibited_until = datetime('now', '-1 day') "
            "WHERE fact_id = 'hold1'"
        )
        conn.commit()
    result = store.apply_decay("u1")
    assert result["restored"] >= 1
    fact = store.get_fact("hold1")
    assert fact["state"] == "active"
    assert fact["inhibited_until"] is None


# ── get_facts state filter ──────────────────────────────────────


def test_get_facts_filters_by_state(store):
    store.get_or_create_user("u1", "u1")
    store.add_fact(fact_id="a1", user_id="u1", content="aktif")
    store.add_fact(fact_id="i1", user_id="u1", content="inhibited")
    store.inhibit_fact("i1")

    active = store.get_facts("u1", state="active")
    inhibited = store.get_facts("u1", state="inhibited")
    assert {f["fact_id"] for f in active} == {"a1"}
    assert {f["fact_id"] for f in inhibited} == {"i1"}
