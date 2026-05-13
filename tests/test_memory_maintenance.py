"""Tests for Faz 22E Step 2 — automatic memory maintenance scheduling.

Validates:
- ``_ensure_maintenance_jobs`` registers exactly two recurring tasks per
  user (daily + weekly) with the configured cron expressions.
- Subsequent calls are idempotent (no duplicate task rows).
- ``memory.maintenance.enabled=false`` short-circuits the bootstrap
  entirely.
- ``memory.enabled=false`` short-circuits the bootstrap.
- The ``memory_maintenance`` processor in the cron scheduler dispatches
  to ``MemoryMaintenance.run_daily/run_weekly/run_now`` correctly.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from gbot.api.app import _ensure_maintenance_jobs
from gbot.core.config.schema import (
    AssistantConfig,
    Config,
    MemoryConfig,
    MemoryMaintenanceConfig,
    OwnerConfig,
)
from gbot.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    db = MemoryStore(str(tmp_path / "maint.db"))
    db.get_or_create_user("u1", "Alice")
    db.get_or_create_user("u2", "Bob")
    # Faz 22J post-bootstrap filter (v1.25.2) only registers maintenance
    # jobs for owner-role users. The legacy tests in this file predate
    # that filter and assert two-user behaviour, so we promote both
    # fixture users to ``owner`` to preserve the original test intent.
    db.set_user_role("u1", "owner")
    db.set_user_role("u2", "owner")
    return db


def _cfg(
    *,
    memory_enabled: bool = True,
    maintenance_enabled: bool = True,
    daily: str = "0 4 * * *",
    weekly: str = "30 4 * * 0",
) -> Config:
    return Config(
        assistant=AssistantConfig(
            owner=OwnerConfig(username="owner", name="Owner"),
        ),
        memory=MemoryConfig(
            enabled=memory_enabled,
            maintenance=MemoryMaintenanceConfig(
                enabled=maintenance_enabled,
                daily_cron=daily,
                weekly_cron=weekly,
            ),
        ),
    )


# ── Bootstrap ────────────────────────────────────────────────────


def test_creates_daily_and_weekly_per_user(store):
    cfg = _cfg()
    _ensure_maintenance_jobs(cfg, store)

    daily_u1 = store.get_task("daily-maintenance-u1")
    weekly_u1 = store.get_task("weekly-maintenance-u1")
    daily_u2 = store.get_task("daily-maintenance-u2")
    weekly_u2 = store.get_task("weekly-maintenance-u2")

    assert daily_u1 is not None
    assert weekly_u1 is not None
    assert daily_u2 is not None
    assert weekly_u2 is not None

    assert daily_u1["execution_type"] == "recurring"
    assert daily_u1["processor"] == "memory_maintenance"
    assert daily_u1["cron_expr"] == "0 4 * * *"
    assert json.loads(daily_u1["plan_json"]) == {"kind": "daily"}

    assert weekly_u1["cron_expr"] == "30 4 * * 0"
    assert json.loads(weekly_u1["plan_json"]) == {"kind": "weekly"}


def test_idempotent_on_repeated_calls(store):
    cfg = _cfg()
    _ensure_maintenance_jobs(cfg, store)
    _ensure_maintenance_jobs(cfg, store)
    _ensure_maintenance_jobs(cfg, store)

    # Still exactly one row per task_id (UNIQUE on PRIMARY KEY would
    # have raised; we double-check with a count).
    all_tasks = store.get_tasks(execution_type="recurring")
    maint = [t for t in all_tasks if t["processor"] == "memory_maintenance"]
    assert len(maint) == 4  # 2 users × (daily + weekly)


def test_skipped_when_maintenance_disabled(store):
    cfg = _cfg(maintenance_enabled=False)
    _ensure_maintenance_jobs(cfg, store)
    assert store.get_task("daily-maintenance-u1") is None
    assert store.get_task("weekly-maintenance-u1") is None


def test_skipped_when_memory_disabled(store):
    cfg = _cfg(memory_enabled=False)
    _ensure_maintenance_jobs(cfg, store)
    assert store.get_task("daily-maintenance-u1") is None


def test_custom_cron_honored(store):
    cfg = _cfg(daily="*/15 * * * *", weekly="0 */6 * * 0")
    _ensure_maintenance_jobs(cfg, store)
    daily = store.get_task("daily-maintenance-u1")
    weekly = store.get_task("weekly-maintenance-u1")
    assert daily["cron_expr"] == "*/15 * * * *"
    assert weekly["cron_expr"] == "0 */6 * * 0"


# ── Scheduler processor dispatch ────────────────────────────────


@pytest.mark.asyncio
async def test_processor_dispatches_to_run_daily(store):
    """memory_maintenance with kind='daily' calls MemoryMaintenance.run_daily."""
    from gbot.core.cron.scheduler import CronScheduler

    cfg = _cfg()
    # Minimal runner stub
    class _StubRunner:
        async def process(self, **kwargs):
            return ("", None)

    scheduler = CronScheduler(store, _StubRunner(), config=cfg)

    fake_stats = {"user_id": "u1", "kind": "daily", "decay": {"faded": 0, "archived": 0, "by_type": {}}}
    with patch(
        "gbot.memory.maintenance.MemoryMaintenance.run_daily",
        new_callable=AsyncMock,
        return_value=fake_stats,
    ) as mock_daily:
        text, deliver = await scheduler._run_by_processor(
            processor="memory_maintenance",
            plan={"kind": "daily"},
            message="memory daily",
            user_id="u1",
            channel="api",
        )

    mock_daily.assert_awaited_once_with("u1")
    assert deliver is False  # internal job, no delivery
    assert "memory_maintenance" in text


@pytest.mark.asyncio
async def test_processor_dispatches_to_run_weekly(store):
    from gbot.core.cron.scheduler import CronScheduler

    cfg = _cfg()
    class _StubRunner:
        async def process(self, **kwargs):
            return ("", None)

    scheduler = CronScheduler(store, _StubRunner(), config=cfg)

    fake_stats = {"user_id": "u1", "kind": "weekly", "relations_deduped": 0}
    with patch(
        "gbot.memory.maintenance.MemoryMaintenance.run_weekly",
        new_callable=AsyncMock,
        return_value=fake_stats,
    ) as mock_weekly:
        text, deliver = await scheduler._run_by_processor(
            processor="memory_maintenance",
            plan={"kind": "weekly"},
            message="memory weekly",
            user_id="u1",
            channel="api",
        )

    mock_weekly.assert_awaited_once_with("u1")
    assert deliver is False


@pytest.mark.asyncio
async def test_processor_default_kind_is_daily(store):
    """If plan has no 'kind', default to daily."""
    from gbot.core.cron.scheduler import CronScheduler

    cfg = _cfg()
    class _StubRunner:
        async def process(self, **kwargs):
            return ("", None)

    scheduler = CronScheduler(store, _StubRunner(), config=cfg)

    with patch(
        "gbot.memory.maintenance.MemoryMaintenance.run_daily",
        new_callable=AsyncMock,
        return_value={"kind": "daily"},
    ) as mock_daily:
        await scheduler._run_by_processor(
            processor="memory_maintenance",
            plan={},  # no kind
            message="x",
            user_id="u1",
            channel="api",
        )
    mock_daily.assert_awaited_once()
