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
        """Weekly housekeeping: relations dedup catch-up + Faz 22J page lint."""
        stats: dict[str, Any] = {
            "user_id": user_id,
            "kind": "weekly",
            "relations_deduped": self._dedup_live_relations(user_id),
        }
        # Faz 22J — wiki page lint pass (stale citations, orphan pages).
        page_cfg = getattr(self.config, "entity_pages", None) if self.config else None
        if page_cfg and getattr(page_cfg, "lint_enabled", True):
            try:
                stats["lint"] = await self.lint_pages(user_id)
            except Exception as e:  # pragma: no cover — defensive
                logger.warning(f"lint_pages failed for {user_id}: {e}")
                stats["lint"] = {"error": str(e)}
        logger.info(f"memory weekly maintenance: {stats}")
        return stats

    async def run_now(self, user_id: str) -> dict[str, Any]:
        """Run both passes immediately. Used by the admin endpoint."""
        return {
            "daily": await self.run_daily(user_id),
            "weekly": await self.run_weekly(user_id),
        }

    # ── Faz 22J — Wiki page lint ────────────────────────────────

    async def lint_pages(self, user_id: str) -> dict[str, Any]:
        """Sweep entity pages for issues that drift in over time.

        Looks for:
          - **Stale citations**: ``[fact_id:xxxxxxxx]`` references whose
            underlying fact has been archived. Marks the page stale so
            the next compile drops the citation.
          - **Pages without source facts**: zero valid source_fact_ids
            (orphans). Enqueues a recompile (`_compile` will skip-or-
            archive based on current valid set).

        Returns counts. Idempotent — safe to schedule weekly.
        """
        if not self.compiler:
            return {"user_id": user_id, "enabled": False}

        import json as _json
        import re

        cite_re = re.compile(r"\[fact_id:([0-9a-fA-F]{6,16})\]")

        with self.db._get_conn() as conn:
            pages = conn.execute(
                """SELECT page_id, entity_canonical, content_md, source_fact_ids
                       FROM memory_entity_pages
                       WHERE user_id = ?""",
                (user_id,),
            ).fetchall()

        stale_marked = 0
        orphan_enqueued = 0
        total_stale_cites = 0

        for p in pages:
            content = p["content_md"] or ""
            ids = cite_re.findall(content)
            if not ids:
                # Page exists with zero citations — borderline, skip.
                continue

            # Look up each cited fact: archived or missing → stale citation.
            with self.db._get_conn() as conn:
                placeholders = ",".join("?" * len(ids))
                rows = conn.execute(
                    f"""SELECT fact_id, valid_until FROM memory_facts
                            WHERE fact_id IN ({placeholders}) AND user_id = ?""",
                    [*ids, user_id],
                ).fetchall()
            valid_by_id = {r["fact_id"]: r["valid_until"] is None for r in rows}
            stale_for_page = [
                fid for fid in ids
                if not valid_by_id.get(fid, False)  # missing or archived
            ]
            if stale_for_page:
                total_stale_cites += len(stale_for_page)
                # Mark stale; next compile will rewrite the section without
                # the dead citations.
                try:
                    self.db.mark_entity_pages_stale(user_id, p["entity_canonical"])
                    stale_marked += 1
                except Exception:
                    pass

            # Orphan check — every source fact archived?
            try:
                src_ids = _json.loads(p["source_fact_ids"] or "[]")
            except (ValueError, TypeError):
                src_ids = []
            if src_ids:
                with self.db._get_conn() as conn:
                    placeholders = ",".join("?" * len(src_ids))
                    valid_row = conn.execute(
                        f"""SELECT COUNT(*) FROM memory_facts
                                WHERE fact_id IN ({placeholders})
                                  AND valid_until IS NULL""",
                        src_ids,
                    ).fetchone()
                if valid_row and valid_row[0] == 0:
                    try:
                        await self.compiler.enqueue(user_id, p["entity_canonical"])
                        orphan_enqueued += 1
                    except Exception:
                        pass

        stats = {
            "user_id": user_id,
            "kind": "lint",
            "pages_scanned": len(pages),
            "pages_marked_stale": stale_marked,
            "stale_citations": total_stale_cites,
            "orphans_enqueued": orphan_enqueued,
        }
        logger.info(f"memory page lint: {stats}")
        return stats

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
