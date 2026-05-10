"""LLM-compiled entity pages (Faz 22D — Karpathy LLM-Wiki pattern).

Each canonical entity (a person, place, organisation, tool — anything that
appears in ``memory_relations``) gets a compact markdown summary derived
from its facts and relations. The agent reads the page instead of a flat
fact dump, which is more token-efficient and semantically organised.

Workflow:

1. After ``MemoryService.extract_and_save`` adds/updates facts,
   ``EntityPageCompiler.enqueue`` is called for each touched canonical
   entity. The page is marked ``stale=1`` immediately.
2. A 60-second debounce coalesces rapid mentions of the same entity.
3. When the debounce window expires, the compiler:
     - Gathers the entity's valid facts (top-N by importance/access)
     - Gathers its valid relations
     - Asks the LLM to write a compact markdown summary
     - Stores it via ``store.upsert_entity_page``
4. ``ContextBuilder`` reads the page and injects it as the
   ``ENTITY PAGES`` block (Step 7).

Provenance: ``source_fact_ids`` and ``source_relation_ids`` are stored
as JSON. When any of those facts is invalidated, the page is auto-marked
stale via the ``invalidate_fact`` hook in ``MemoryStore``.

Default OFF — set ``memory.entity_pages.enabled: true`` in config.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger

from gbot.core.providers import llm as llm_provider

if TYPE_CHECKING:
    from gbot.core.config.schema import MemoryConfig
    from gbot.memory.entities import EntityResolver
    from gbot.memory.store import MemoryStore


_PAGE_PROMPT = """You are compiling a memory page for the entity below.

Output format:
1. ONE short paragraph (max 80 words) — who/what this entity is, their
   relationship to the user, and the most current state.
2. A bullet list of the 3–7 most important facts. Each bullet ends with
   `[fact_id:xxxxxxxx]` citation. Resolve any contradictions toward the
   most recent valid fact and ignore the rest.
3. Markdown only. No greeting, no preamble, no headings beyond the
   bullet list. Match the dominant language of the source facts
   (typically Turkish).

ENTITY: {entity}
ALIASES: {aliases}

RELATIONS (valid only):
{relations}

FACTS (most recent first, valid only):
{facts}
"""


@dataclass
class _ScheduledTask:
    """Pending compile job in the debounce queue."""

    user_id: str
    canonical: str
    deadline: float = 0.0
    handle: asyncio.Task | None = field(default=None, repr=False)


class EntityPageCompiler:
    """Async, debounced LLM compiler for entity pages.

    The compiler is process-local (single-worker assumption). For
    multi-worker deployments, replace the in-process queue with the
    ``background_tasks`` table (Faz 22E).
    """

    def __init__(
        self,
        db: "MemoryStore",
        config: "MemoryConfig",
        resolver: "EntityResolver | None" = None,
        model: str | None = None,
    ):
        self.db = db
        self.config = config
        self.resolver = resolver
        page_cfg = getattr(config, "entity_pages", None)
        self.model = (
            model
            or (page_cfg.model if page_cfg else None)
            or "openrouter/openai/gpt-4o-mini"
        )
        self._pending: dict[tuple[str, str], _ScheduledTask] = {}
        self._lock = asyncio.Lock()

    # ── Public API ────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        page_cfg = getattr(self.config, "entity_pages", None)
        return bool(page_cfg and page_cfg.enabled)

    async def enqueue(self, user_id: str, canonical: str) -> None:
        """Schedule a compile for ``canonical`` after the debounce window.

        Idempotent — if the entity is already pending, the deadline is
        pushed forward; the prior task is cancelled. Marks the page stale
        immediately so the read path knows it's out-of-date.
        """
        if not self.enabled or not canonical:
            return

        page_cfg = self.config.entity_pages
        delay = max(1, page_cfg.debounce_seconds)

        # Mark stale right away — read path uses this for fallback decisions.
        try:
            self.db.mark_entity_pages_stale(user_id, canonical)
        except Exception as e:  # pragma: no cover — defensive
            logger.debug(f"mark_stale failed for {canonical}: {e}")

        key = (user_id, canonical)
        async with self._lock:
            existing = self._pending.get(key)
            if existing and existing.handle and not existing.handle.done():
                existing.handle.cancel()
            task = _ScheduledTask(user_id=user_id, canonical=canonical)
            task.deadline = time.monotonic() + delay
            task.handle = asyncio.create_task(self._wait_then_compile(task, delay))
            self._pending[key] = task

    async def compile_now(
        self, user_id: str, canonical: str
    ) -> dict[str, Any] | None:
        """Compile immediately, bypassing the debounce queue. Used by the
        admin recompile endpoint and by tests.
        """
        if not self.enabled:
            return None
        return await self._compile(user_id, canonical)

    # ── Internal ──────────────────────────────────────────────────

    async def _wait_then_compile(
        self, task: _ScheduledTask, delay: int
    ) -> None:
        try:
            await asyncio.sleep(delay)
            await self._compile(task.user_id, task.canonical)
        except asyncio.CancelledError:  # pragma: no cover — coalesced
            return
        except Exception as e:  # pragma: no cover — defensive
            logger.warning(f"entity-page compile failed for {task.canonical}: {e}")
        finally:
            async with self._lock:
                self._pending.pop((task.user_id, task.canonical), None)

    async def _compile(
        self, user_id: str, canonical: str
    ) -> dict[str, Any] | None:
        """Gather context → call LLM → upsert page. Returns the page row
        or None if not eligible / failed.
        """
        page_cfg = self.config.entity_pages

        # Gather facts that mention this entity. Two sources:
        # (1) facts whose content contains a known surface form for the
        #     canonical, (2) facts referenced by relations on this entity.
        surface_forms = self._surface_forms(user_id, canonical)
        facts = self._gather_facts(user_id, surface_forms)
        relations = self._gather_relations(user_id, canonical)

        # Eligibility threshold — don't waste LLM calls on weak entities.
        if (
            len(facts) < page_cfg.min_facts_for_page
            and len(relations) < page_cfg.min_relations_for_page
        ):
            logger.debug(
                f"entity_pages skip {canonical}: "
                f"{len(facts)} facts / {len(relations)} relations below threshold"
            )
            return None

        # Skip if compiled recently (avoid double-work after a cluster of
        # invalidations marks the same page stale repeatedly).
        existing = self.db.get_entity_page(user_id, canonical)
        if existing and existing.get("stale") == 0:
            try:
                from datetime import datetime, timedelta

                last = datetime.fromisoformat(existing["last_compiled_at"])
                if datetime.now() - last < timedelta(minutes=5):
                    logger.debug(
                        f"entity_pages skip {canonical}: compiled <5 min ago"
                    )
                    return existing
            except (ValueError, TypeError, KeyError):
                pass

        # Render LLM prompt
        prompt = _PAGE_PROMPT.format(
            entity=canonical,
            aliases=", ".join(sorted(surface_forms)) if surface_forms else canonical,
            relations=self._format_relations(relations),
            facts=self._format_facts(facts),
        )

        try:
            response = await llm_provider.achat(
                messages=[{"role": "user", "content": prompt}],
                model=self.model,
                temperature=0.2,
                max_tokens=page_cfg.max_output_tokens,
            )
            content_md = (response.content or "").strip()
        except Exception as e:
            logger.warning(f"entity-page LLM failed for {canonical}: {e}")
            return None

        if not content_md:
            return None

        page_id = self.db.upsert_entity_page(
            user_id=user_id,
            entity_canonical=canonical,
            content_md=content_md,
            source_fact_ids=[f["fact_id"] for f in facts],
            source_relation_ids=[r["relation_id"] for r in relations],
            surface_forms=sorted(surface_forms),
        )
        logger.info(
            f"entity-page compiled: {canonical} (page_id={page_id}, "
            f"{len(facts)} facts, {len(relations)} relations)"
        )
        return self.db.get_entity_page(user_id, canonical)

    # ── Helpers ───────────────────────────────────────────────────

    def _surface_forms(self, user_id: str, canonical: str) -> set[str]:
        """All known surface forms for the canonical entity."""
        if self.resolver:
            return self.resolver.expand(user_id, canonical)
        return {canonical}

    def _gather_facts(
        self, user_id: str, surface_forms: set[str]
    ) -> list[dict[str, Any]]:
        """Find valid facts mentioning any surface form."""
        if not surface_forms:
            return []
        all_facts = self.db.get_facts(user_id, valid_only=True, limit=500)
        forms_lower = {s.lower() for s in surface_forms if s}
        matched = []
        for f in all_facts:
            content = (f.get("content") or "").lower()
            if any(form in content for form in forms_lower):
                matched.append(f)
        # Top by importance × access (most recent first as tiebreaker)
        matched.sort(
            key=lambda f: (
                (f.get("importance") or 0.5) * (1 + (f.get("access_count") or 0) / 10),
                f.get("created_at") or "",
            ),
            reverse=True,
        )
        page_cfg = self.config.entity_pages
        max_facts = max(5, page_cfg.max_input_tokens // 50)  # rough heuristic
        return matched[:max_facts]

    def _gather_relations(
        self, user_id: str, canonical: str
    ) -> list[dict[str, Any]]:
        """All valid relations involving the canonical entity."""
        return self.db.get_relations(user_id, canonical=canonical, limit=50)

    @staticmethod
    def _format_facts(facts: list[dict[str, Any]]) -> str:
        if not facts:
            return "(none)"
        lines: list[str] = []
        for f in facts:
            fid = f.get("fact_id", "")[:8]
            content = f.get("content") or ""
            imp = f.get("importance") or 0.5
            acc = f.get("access_count") or 0
            lines.append(
                f"- [{fid}] (imp={imp:.2f} acc={acc}) {content}"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_relations(relations: list[dict[str, Any]]) -> str:
        if not relations:
            return "(none)"
        lines: list[str] = []
        seen: set[tuple[str, str, str]] = set()
        for r in relations:
            src = r.get("canonical_source") or r.get("source_entity") or ""
            rel = r.get("relation") or ""
            tgt = r.get("canonical_target") or r.get("target_entity") or ""
            key = (src, rel, tgt)
            if key in seen:
                continue
            seen.add(key)
            rid = r.get("relation_id", "")[:8]
            lines.append(f"- [{rid}] {src} → {rel} → {tgt}")
        return "\n".join(lines)
