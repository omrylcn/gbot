"""Tests for EntityResolver (Faz 22D — entity normalization)."""

from __future__ import annotations

import pytest

from gbot.memory.entities import EntityResolver
from gbot.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    db = MemoryStore(str(tmp_path / "entities.db"))
    db.get_or_create_user("u1", "Ömer")
    return db


@pytest.fixture
def resolver(store):
    return EntityResolver(store, owner_username="u1", owner_display_name="Ömer")


def test_owner_aliases_resolve_to_canonical(resolver):
    """Self-reference words map to owner.username."""
    assert resolver.canonicalize("u1", "Ömer") == "u1"
    assert resolver.canonicalize("u1", "Kullanıcı") == "u1"
    assert resolver.canonicalize("u1", "User") == "u1"
    assert resolver.canonicalize("u1", "owner") == "u1"
    assert resolver.canonicalize("u1", "ben") == "u1"
    assert resolver.canonicalize("u1", "me") == "u1"


def test_unknown_entity_returns_identity(resolver):
    """Unknown surface forms map to themselves (preserved for audit)."""
    assert resolver.canonicalize("u1", "Murat") == "Murat"
    assert resolver.canonicalize("u1", "Acme Corp") == "Acme Corp"


def test_whitespace_and_punct_trimmed(resolver):
    """Surrounding whitespace and trailing punctuation don't break tier-1."""
    assert resolver.canonicalize("u1", "  Ömer.") == "u1"
    assert resolver.canonicalize("u1", '"User"') == "u1"
    assert resolver.canonicalize("u1", "\tUser\n") == "u1"


def test_idempotent(resolver):
    """canonicalize(canonicalize(x)) == canonicalize(x)."""
    once = resolver.canonicalize("u1", "Ömer")
    twice = resolver.canonicalize("u1", once)
    assert once == twice


def test_alias_table_roundtrip(resolver):
    """register_alias persists and is read back by canonicalize."""
    resolver.register_alias("u1", "Mr. Murat", "Murat", source="manual")
    assert resolver.canonicalize("u1", "Mr. Murat") == "Murat"


def test_expand_includes_canonical_self_and_owner_terms(resolver):
    """expand() returns all surface forms + the canonical itself."""
    forms = resolver.expand("u1", "u1")
    assert "u1" in forms
    # Owner anchors merged in
    assert "user" in {f.lower() for f in forms}


def test_merge_canonicals_rewrites_relations(store, resolver):
    """merge_canonicals updates canonical_source/canonical_target on relations."""
    store.add_relation(
        "rel-a", "u1", "Ömer", "knows", "Murat",
        canonical_source="omer-old", canonical_target="Murat",
    )
    store.add_relation(
        "rel-b", "u1", "Murat", "knows", "Ömer",
        canonical_source="Murat", canonical_target="omer-old",
    )

    n = resolver.merge_canonicals("u1", "omer-old", "u1")
    assert n == 2

    rels = store.get_relations("u1", canonical="u1")
    assert len(rels) == 2


def test_backfill_does_not_clobber_raw(store, resolver):
    """Backfill only writes canonical_* — never modifies source_entity."""
    store.add_relation("rel-c", "u1", "Ömer", "knows", "Murat")
    resolver.backfill_relations("u1")
    rels = store.get_relations("u1")
    # Raw form preserved
    assert rels[0]["source_entity"] == "Ömer"
    # Canonical written
    assert rels[0]["canonical_source"] == "u1"
    assert rels[0]["canonical_target"] == "Murat"


def test_backfill_idempotent(store, resolver):
    """Backfill skips already-canonicalized rows on re-run."""
    store.add_relation("rel-d", "u1", "Ömer", "knows", "Murat")
    n1 = resolver.backfill_relations("u1")
    n2 = resolver.backfill_relations("u1")
    assert n1 == 1 and n2 == 0


def test_no_owner_config_falls_back_to_identity(store):
    """When owner_username is empty, owner-aliases tier is silent."""
    r = EntityResolver(store, owner_username=None)
    # "Ömer" is no longer an owner anchor — returns itself
    assert r.canonicalize("u1", "Ömer") == "Ömer"
    # Self-reference words still don't crash, fall through to identity
    assert r.canonicalize("u1", "User") == "User"
