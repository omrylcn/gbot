"""Faz 22G Aşama 2 — persona / style memory.

Style fact_type, decay rate, ContextBuilder STYLE block injection.
"""

from __future__ import annotations

import pytest

from gbot.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(str(tmp_path / "persona.db"))


def test_style_fact_type_accepted(store):
    store.get_or_create_user("u1", "u1")
    store.add_fact(
        fact_id="s1",
        user_id="u1",
        content="Kullanıcı kısa, doğrudan cevaplar tercih ediyor",
        fact_type="style",
        category="style",
        importance=0.7,
    )
    fact = store.get_fact("s1")
    assert fact is not None
    assert fact["fact_type"] == "style"
    assert fact["state"] == "active"


def test_style_decay_rates_present(store):
    """The DEFAULT_DECAY_RATES table must include 'style' so apply_decay
    doesn't silently drop style facts.
    """
    rates = store._DEFAULT_DECAY_RATES
    assert "style" in rates
    style_rate = rates["style"]
    # Style ages slowest — fade_days should be >= preference (120).
    assert style_rate["fade_days"] >= 120
    assert style_rate["archive_days"] >= 365


def test_get_facts_style_filter(store):
    store.get_or_create_user("u1", "u1")
    store.add_fact(
        fact_id="sem1", user_id="u1", content="işi developer", fact_type="semantic"
    )
    store.add_fact(
        fact_id="sty1", user_id="u1", content="kısa cevap tercih ediyor", fact_type="style"
    )
    style_only = store.get_facts("u1", fact_type="style")
    assert {f["fact_id"] for f in style_only} == {"sty1"}


def test_apply_decay_handles_style_type(store):
    """A 'style' fact older than its (large) fade window flips to weak,
    not crashes for missing rate.
    """
    store.get_or_create_user("u1", "u1")
    store.add_fact(
        fact_id="old_style", user_id="u1",
        content="emoji kullanmıyor", fact_type="style",
        importance=0.6,
    )
    # Push created_at way back so the 180-day fade threshold trips.
    with store._get_conn() as conn:
        conn.execute(
            "UPDATE memory_facts SET created_at = datetime('now', '-400 days')"
        )
        conn.commit()
    result = store.apply_decay("u1")
    # Style was pushed past the fade window
    assert result["faded"] >= 1
    fact = store.get_fact("old_style")
    assert fact["state"] in ("weak", "archived")
