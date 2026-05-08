"""Periodic memory maintenance (Faz 22D Step 12).

Replaces the dead ``consolidation.py``. The previous "merge overlapping
facts" pass duplicated work AUDN already does at ingest, so this module
keeps only the genuinely periodic concerns:

- **Daily** — type-aware decay (Step 11), stale-page recompile catch-up,
  orphan entity cleanup.
- **Weekly** — relations dedup catch-up (in case rows leaked in before
  the UNIQUE constraint), optional VACUUM.

Designed to be called from the unified ``background_tasks`` scheduler
or directly via the admin API. No background thread of its own — the
scheduler controls cadence so users can pause/inspect it like any other
recurring task.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from gbot.core.config.schema import MemoryConfig
    from gbot.memory.entity_pages import EntityPageCompiler
    from gbot.memory.store import MemoryStore


class MemoryMaintenance:
    """Periodic memory housekeeping for a single user.

    Construction is cheap; the real work happens in ``run_daily`` /
    ``run_weekly`` / ``run_now``. Each pass returns a stats dict so the
    caller (admin endpoint, cron job) can log/report.
    """

    def __init__(
        self,
        db: "MemoryStore",
        config: "MemoryConfig",
        compiler: "EntityPageCompiler | None" = None,
    ):
        self.db = db
        self.config = config
        self.compiler = compiler

    # ── Public passes ─────────────────────────────────────────────

    async def run_daily(self, user_id: str) -> dict[str, Any]:
        """Daily housekeeping: decay + stale-page catch-up + orphan cleanup."""
        stats: dict[str, Any] = {
            "user_id": user_id,
            "kind": "daily",
        }

        # 1. Type-aware decay
        try:
            decay = self.db.apply_decay(user_id)
            stats["decay"] = decay
        except Exception as e:  # pragma: no cover — defensive
            logger.warning(f"decay failed for {user_id}: {e}")
            stats["decay"] = {"error": str(e)}

        # 2. Stale page recompile catch-up. The debouncer covers the hot
        #    path; this catches anything dropped by process restart.
        stale_handled = await self._recompile_stale_pages(user_id)
        stats["pages_recompiled"] = stale_handled

        # 3. Orphan cleanup — pages whose every source fact got
        #    invalidated lose their reason to exist.
        orphans = self._delete_orphan_pages(user_id)
        stats["orphan_pages_deleted"] = orphans

        logger.info(f"memory daily maintenance: {stats}")
        return stats

    async def run_weekly(self, user_id: str) -> dict[str, Any]:
        """Weekly housekeeping: relations dedup catch-up."""
        stats: dict[str, Any] = {
            "user_id": user_id,
            "kind": "weekly",
            "relations_deduped": self._dedup_live_relations(user_id),
        }
        logger.info(f"memory weekly maintenance: {stats}")
        return stats

    async def run_now(self, user_id: str) -> dict[str, Any]:
        """Run both passes immediately. Used by the admin endpoint."""
        return {
            "daily": await self.run_daily(user_id),
            "weekly": await self.run_weekly(user_id),
        }

    # ── Internals ─────────────────────────────────────────────────

    async def _recompile_stale_pages(self, user_id: str) -> int:
        """Recompile pages flagged ``stale=1``. Returns count compiled."""
        if not self.compiler or not self.compiler.enabled:
            return 0
        with self.db._get_conn() as conn:
            rows = conn.execute(
                """SELECT entity_canonical FROM memory_entity_pages
                       WHERE user_id = ? AND stale = 1""",
                (user_id,),
            ).fetchall()
        compiled = 0
        for r in rows:
            try:
                result = await self.compiler.compile_now(user_id, r["entity_canonical"])
                if result is not None:
                    compiled += 1
            except Exception as e:  # pragma: no cover — defensive
                logger.warning(
                    f"recompile failed for {r['entity_canonical']}: {e}"
                )
        return compiled

    def _delete_orphan_pages(self, user_id: str) -> int:
        """Delete pages whose every source_fact_id has been invalidated."""
        with self.db._get_conn() as conn:
            pages = conn.execute(
                """SELECT page_id, entity_canonical, source_fact_ids
                       FROM memory_entity_pages
                       WHERE user_id = ?""",
                (user_id,),
            ).fetchall()

        import json as _json

        deleted = 0
        for p in pages:
            try:
                fact_ids = _json.loads(p["source_fact_ids"] or "[]")
            except (ValueError, TypeError):
                continue
            if not fact_ids:
                continue
            with self.db._get_conn() as conn:
                placeholders = ",".join("?" * len(fact_ids))
                row = conn.execute(
                    f"""SELECT COUNT(*) FROM memory_facts
                            WHERE fact_id IN ({placeholders})
                              AND valid_until IS NULL""",
                    fact_ids,
                ).fetchone()
            valid_remaining = row[0] if row else 0
            if valid_remaining == 0:
                self.db.delete_entity_page(user_id, p["entity_canonical"])
                deleted += 1
        return deleted

    def _dedup_live_relations(self, user_id: str) -> int:
        """Sweep stragglers — any live duplicate triples that slipped past
        the UNIQUE index (shouldn't happen, but cheap insurance).
        """
        with self.db._get_conn() as conn:
            cursor = conn.execute(
                """DELETE FROM memory_relations
                       WHERE user_id = ?
                         AND valid_until IS NULL
                         AND rowid NOT IN (
                             SELECT MIN(rowid) FROM memory_relations
                             WHERE user_id = ? AND valid_until IS NULL
                             GROUP BY user_id, source_entity, relation, target_entity
                         )""",
                (user_id, user_id),
            )
            deduped = cursor.rowcount
            conn.commit()
        return deduped
