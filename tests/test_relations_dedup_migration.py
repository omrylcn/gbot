"""Tests for Faz 22D schema migration — relations dedup, UNIQUE index,
new tables (entity_pages, entity_aliases), idempotency.
"""

from __future__ import annotations

import sqlite3

import pytest

from gbot.memory.store import MemoryStore


def _seed_legacy_db(path: str, n_dups: int = 5) -> None:
    """Build a pre-22D DB: memory_relations without canonical_* and with
    duplicate live rows on the same triple. Bypass MemoryStore so the
    migration doesn't run.
    """
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE memory_relations (
               relation_id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               source_entity TEXT NOT NULL,
               relation TEXT NOT NULL,
               target_entity TEXT NOT NULL,
               confidence REAL DEFAULT 1.0,
               valid_from TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
               valid_until TIMESTAMP,
               source_fact TEXT,
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    # Same (user, source, rel, target) repeated n_dups times — all live.
    for i in range(n_dups):
        conn.execute(
            "INSERT INTO memory_relations (relation_id, user_id, source_entity, relation, target_entity) "
            "VALUES (?, ?, ?, ?, ?)",
            (f"r{i}", "u1", "Ömer", "knows", "Murat"),
        )
    # An invalidated dup — must NOT be deleted (only live rows are deduped).
    conn.execute(
        "INSERT INTO memory_relations (relation_id, user_id, source_entity, relation, target_entity, valid_until) "
        "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
        ("r-old", "u1", "Ömer", "knows", "Murat"),
    )
    # A different unique triple — must survive untouched.
    conn.execute(
        "INSERT INTO memory_relations (relation_id, user_id, source_entity, relation, target_entity) "
        "VALUES (?, ?, ?, ?, ?)",
        ("r-other", "u1", "Murat", "knows", "Zeynep"),
    )
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()


def test_dedup_collapses_live_dups(tmp_path):
    """Migration collapses N duplicate live rows on the same triple into 1."""
    path = str(tmp_path / "legacy.db")
    _seed_legacy_db(path, n_dups=5)

    # Verify pre-state
    conn = sqlite3.connect(path)
    pre = conn.execute(
        "SELECT COUNT(*) FROM memory_relations WHERE valid_until IS NULL"
    ).fetchone()[0]
    conn.close()
    assert pre == 6  # 5 dups + 1 unique

    # Run migration via MemoryStore init
    MemoryStore(path)

    conn = sqlite3.connect(path)
    post_live = conn.execute(
        "SELECT COUNT(*) FROM memory_relations WHERE valid_until IS NULL"
    ).fetchone()[0]
    post_total = conn.execute("SELECT COUNT(*) FROM memory_relations").fetchone()[0]
    conn.close()
    assert post_live == 2  # 1 deduped + 1 untouched unique
    assert post_total == 3  # + 1 invalidated dup preserved


def test_unique_index_created(tmp_path):
    """Partial UNIQUE index `uq_relations_active` exists after migration."""
    path = str(tmp_path / "fresh.db")
    MemoryStore(path)
    conn = sqlite3.connect(path)
    idx = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='uq_relations_active'"
    ).fetchone()
    conn.close()
    assert idx is not None


def test_canonical_columns_added(tmp_path):
    """Migration adds canonical_source / canonical_target to legacy schema."""
    path = str(tmp_path / "legacy.db")
    _seed_legacy_db(path, n_dups=1)
    MemoryStore(path)
    conn = sqlite3.connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(memory_relations)").fetchall()}
    conn.close()
    assert "canonical_source" in cols
    assert "canonical_target" in cols


def test_new_tables_created(tmp_path):
    """Migration creates memory_entity_pages and memory_entity_aliases."""
    path = str(tmp_path / "fresh.db")
    MemoryStore(path)
    conn = sqlite3.connect(path)
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    assert "memory_entity_pages" in tables
    assert "memory_entity_aliases" in tables


def test_user_version_set(tmp_path):
    """PRAGMA user_version reaches the latest schema version after init;
    idempotent on re-run. Faz 22G bumped this to 23.
    """
    path = str(tmp_path / "v.db")
    MemoryStore(path)
    conn = sqlite3.connect(path)
    v1 = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert v1 == 23

    # Re-init — version stays put, no double-migration.
    MemoryStore(path)
    conn = sqlite3.connect(path)
    v2 = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert v2 == 23


def test_unique_collision_updates_metadata(tmp_path):
    """add_relation on existing live triple updates confidence + source_fact
    instead of inserting a duplicate row.
    """
    path = str(tmp_path / "u.db")
    db = MemoryStore(path)
    db.get_or_create_user("u1", "Test")

    db.add_relation("ra", "u1", "A", "knows", "B", confidence=0.8)
    db.add_relation("rb", "u1", "A", "knows", "B", confidence=0.5, source_fact="f1")

    rels = db.get_relations("u1")
    assert len(rels) == 1
    assert rels[0]["confidence"] == 0.5
    assert rels[0]["source_fact"] == "f1"


def test_reassert_after_invalidate_succeeds(tmp_path):
    """Invalidating a relation lets the same triple be asserted again."""
    path = str(tmp_path / "ri.db")
    db = MemoryStore(path)
    db.get_or_create_user("u1", "Test")

    db.add_relation("ra", "u1", "A", "knows", "B")
    rels = db.get_relations("u1")
    db.invalidate_relation(rels[0]["relation_id"])

    db.add_relation("rb", "u1", "A", "knows", "B")
    valid = db.get_relations("u1")
    assert len(valid) == 1
    assert valid[0]["relation_id"] == "rb"
