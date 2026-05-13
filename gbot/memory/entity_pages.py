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


# Section names used by the parser. Turkish synonyms accept the same
# semantic slot — the LLM may emit either set.
_CANONICAL_SECTIONS = ("Lead", "Profile", "Interactions", "History")
_SECTION_SYNONYMS = {
    # English canonical → Turkish synonyms (we normalise to the English form
    # in the parser to avoid downstream branches).
    "Lead": ("Lead", "Özet", "Giriş"),
    "Profile": ("Profile", "Profil"),
    "Interactions": ("Interactions", "Etkileşim", "Olaylar"),
    "History": ("History", "Geçmiş", "Tarihçe"),
}


_PAGE_PROMPT_FULL = """You are compiling a memory wiki page for the entity below.

Output **markdown with four sections**, each as an H2 header (use the
English headers verbatim so the parser can find them):

## Lead
ONE short paragraph (target {lead_words} words) — who/what this entity is,
their relationship to the user, and the most current state. Resolve any
contradictions toward the most recent valid fact.

## Profile
Bullet list of stable structural facts (lives_in, works_at, owns,
married_to, partner_of, knows, uses, studies). Each bullet ends with
`[fact_id:xxxxxxxx]` citation when a specific fact backs it. Skip if no
structural facts apply.

## Interactions
Bullet list of recent or notable events with this entity (episodic
facts). Each bullet starts with a date when known (`YYYY-MM-DD —`).
Skip if no episodic facts apply.

## History
Leave empty on a fresh compile. Reserved for superseded claims (filled
on incremental updates only).

Use markdown only — no greeting, no preamble. Match the dominant
language of the source facts (typically Turkish). Total length should
not exceed {budget_tokens} tokens.

ENTITY: {entity}
ALIASES: {aliases}

RELATIONS (valid only):
{relations}

FACTS (most recent first, valid only):
{facts}
"""


_PAGE_PROMPT_INCREMENTAL = """You are **updating** an existing memory wiki
page with new information. The page already has four sections — Lead,
Profile, Interactions, History — and your job is to merge the delta
facts into the right places without rewriting unchanged content.

Rules:
1. **Keep unchanged text verbatim**. Do NOT paraphrase or rephrase the
   existing Lead / Profile / Interactions text unless a delta fact
   directly contradicts a claim there.
2. **Move contradicted claims to ## History** with a dated marker:
   `- ~~old claim~~ (superseded {today})`. Do this only when a delta
   fact replaces a specific Lead/Profile/Interactions bullet.
3. **Append new structural facts** as bullets in ## Profile.
4. **Append new episodic facts** as bullets in ## Interactions, dated.
5. Lead paragraph: only revise if the entity's headline status changed
   (job, location, relationship). Otherwise leave verbatim.
6. Each new bullet ends with `[fact_id:xxxxxxxx]` citation.
7. Markdown only, four sections preserved. Total length cap:
   {budget_tokens} tokens.

ENTITY: {entity}
ALIASES: {aliases}

CURRENT PAGE:
{current_page}

DELTA FACTS (new since last compile):
{delta_facts}

OTHER VALID FACTS (for context, do not duplicate):
{context_facts}

RELATIONS (valid only):
{relations}
"""


# Kept for backward compatibility (some test fixtures import this symbol).
_PAGE_PROMPT = _PAGE_PROMPT_FULL


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
        """Gather context → choose full vs incremental → upsert page +
        append version snapshot. Returns the page row or None if not
        eligible / failed.
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

        # Faz 22J — adaptive size + branch on incremental eligibility.
        pinned = canonical in (getattr(page_cfg, "pinned", []) or [])
        weight = self.db.compute_entity_weight(user_id, canonical)
        budget_tokens, bucket = self._adaptive_max_output_tokens(weight, pinned)

        delta_fact_ids = self._compute_delta_fact_ids(existing, facts)
        use_incremental = bool(
            getattr(page_cfg, "incremental_enabled", True)
            and existing
            and existing.get("sections")
            and 0 < len(delta_fact_ids) <= getattr(page_cfg, "incremental_max_delta", 5)
        )

        if use_incremental:
            content_md, compile_kind = await self._compile_incremental(
                canonical, existing, facts, relations, surface_forms,
                delta_fact_ids, budget_tokens,
            )
        else:
            content_md, compile_kind = await self._compile_full(
                canonical, facts, relations, surface_forms, budget_tokens,
            )

        if not content_md:
            return None

        sections = self._parse_sections(content_md)
        old_sections = (
            json.loads(existing["sections"]) if existing and existing.get("sections") else {}
        )
        section_diff = self._diff_sections(old_sections, sections)

        page_id = self.db.upsert_entity_page(
            user_id=user_id,
            entity_canonical=canonical,
            content_md=content_md,
            source_fact_ids=[f["fact_id"] for f in facts],
            source_relation_ids=[r["relation_id"] for r in relations],
            surface_forms=sorted(surface_forms),
            sections=sections,
            entity_weight=weight,
            size_bucket=bucket,
            last_delta_fact_ids=delta_fact_ids,
        )

        # Append a snapshot to history — append-only, lets us inspect
        # diffs and roll back if needed (Faz 22J).
        try:
            new_page = self.db.get_entity_page(user_id, canonical)
            self.db.insert_page_version(
                page_id=page_id,
                user_id=user_id,
                entity_canonical=canonical,
                version=int(new_page.get("version") if new_page else 1),
                content_md=content_md,
                compile_kind=compile_kind,
                section_diff=section_diff,
                source_fact_ids=[f["fact_id"] for f in facts],
                delta_fact_ids=delta_fact_ids,
                token_budget=budget_tokens,
                output_tokens=len(content_md) // 4,  # rough char/4 heuristic
            )
        except Exception as e:  # pragma: no cover — defensive
            logger.debug(f"insert_page_version failed for {canonical}: {e}")

        logger.info(
            f"entity-page {compile_kind}: {canonical} (page_id={page_id}, "
            f"weight={weight:.2f}, bucket={bucket}, budget={budget_tokens}, "
            f"{len(facts)} facts, {len(relations)} relations, "
            f"delta={len(delta_fact_ids)})"
        )
        return self.db.get_entity_page(user_id, canonical)

    async def _compile_full(
        self,
        canonical: str,
        facts: list[dict[str, Any]],
        relations: list[dict[str, Any]],
        surface_forms: set[str],
        budget_tokens: int,
    ) -> tuple[str, str]:
        """Full rewrite — first compile or contradicted-everything case.
        Returns (content_md, compile_kind)."""
        lead_words = self._target_lead_words(budget_tokens)
        prompt = _PAGE_PROMPT_FULL.format(
            entity=canonical,
            aliases=", ".join(sorted(surface_forms)) if surface_forms else canonical,
            relations=self._format_relations(relations),
            facts=self._format_facts(facts),
            budget_tokens=budget_tokens,
            lead_words=lead_words,
        )
        try:
            response = await llm_provider.achat(
                messages=[{"role": "user", "content": prompt}],
                model=self.model,
                temperature=0.2,
                max_tokens=budget_tokens,
            )
            return (response.content or "").strip(), "full"
        except Exception as e:
            logger.warning(f"entity-page LLM (full) failed for {canonical}: {e}")
            return "", "full"

    async def _compile_incremental(
        self,
        canonical: str,
        existing: dict[str, Any],
        facts: list[dict[str, Any]],
        relations: list[dict[str, Any]],
        surface_forms: set[str],
        delta_fact_ids: list[str],
        budget_tokens: int,
    ) -> tuple[str, str]:
        """Karpathy LLM-Wiki update: keep verbatim, append new bullets,
        move contradictions to ## History. Returns (content_md, kind).

        Falls back to full compile if the LLM appears to have rewritten
        the unchanged Lead paragraph (drift > 50% Jaccard distance).
        """
        from datetime import date

        delta_facts = [f for f in facts if f["fact_id"] in delta_fact_ids]
        context_facts = [f for f in facts if f["fact_id"] not in delta_fact_ids]

        old_sections = json.loads(existing.get("sections") or "{}")
        old_lead = (old_sections.get("Lead") or "").strip()

        prompt = _PAGE_PROMPT_INCREMENTAL.format(
            entity=canonical,
            aliases=", ".join(sorted(surface_forms)) if surface_forms else canonical,
            current_page=existing.get("content_md") or "",
            delta_facts=self._format_facts(delta_facts),
            context_facts=self._format_facts(context_facts[:8]),
            relations=self._format_relations(relations),
            budget_tokens=budget_tokens,
            today=date.today().isoformat(),
        )
        try:
            response = await llm_provider.achat(
                messages=[{"role": "user", "content": prompt}],
                model=self.model,
                temperature=0.2,
                max_tokens=budget_tokens,
            )
            content_md = (response.content or "").strip()
        except Exception as e:
            logger.warning(f"entity-page LLM (incremental) failed for {canonical}: {e}")
            return "", "incremental"

        # Drift guard — if the LLM rewrote the Lead despite no contradicting
        # delta fact, fall back to a full compile so we don't silently lose
        # the existing wording.
        new_sections = self._parse_sections(content_md)
        new_lead = (new_sections.get("Lead") or "").strip()
        if old_lead and new_lead and self._jaccard_distance(old_lead, new_lead) > 0.5:
            logger.warning(
                f"entity-page incremental drift detected for {canonical} — "
                f"Lead rewrote (jaccard>{0.5}); falling back to full compile"
            )
            return await self._compile_full(
                canonical, facts, relations, surface_forms, budget_tokens,
            )

        return content_md, "incremental"

    # ── Faz 22J helpers ───────────────────────────────────────────

    def _adaptive_max_output_tokens(
        self, weight: float, pinned: bool
    ) -> tuple[int, str]:
        """Map entity weight → (max_tokens, bucket_name). Pinned entities
        always use the largest bucket so the user's anchor pages have
        room to breathe."""
        page_cfg = self.config.entity_pages
        buckets = getattr(page_cfg, "size_buckets", None) or {}
        # Sort buckets by weight_max so we pick the smallest container that fits.
        ordered = sorted(buckets.items(), key=lambda kv: kv[1].get("weight_max", 0))
        if pinned and ordered:
            name, conf = ordered[-1]
            return int(conf.get("max_tokens", 3000)), name
        for name, conf in ordered:
            if weight <= float(conf.get("weight_max", 0)):
                return int(conf.get("max_tokens", page_cfg.max_output_tokens)), name
        # Fallback to legacy cap if no bucket matched.
        return int(page_cfg.max_output_tokens), "small"

    def _compute_delta_fact_ids(
        self,
        existing: dict[str, Any] | None,
        facts: list[dict[str, Any]],
    ) -> list[str]:
        """Fact ids present now but not in the last compile's source list."""
        if not existing:
            return [f["fact_id"] for f in facts]
        try:
            prior = set(json.loads(existing.get("source_fact_ids") or "[]"))
        except (ValueError, TypeError):
            prior = set()
        current = [f["fact_id"] for f in facts]
        return [fid for fid in current if fid not in prior]

    def _parse_sections(self, md: str) -> dict[str, str]:
        """Split markdown into the four canonical sections. Accepts
        Turkish synonyms in headers and normalises to English keys.

        Lines before the first recognised H2 are kept under ``"Lead"``
        if Lead wasn't explicitly headed.
        """
        import re

        # Build a synonyms-to-canonical lookup (case-insensitive).
        syn_map: dict[str, str] = {}
        for canonical, synonyms in _SECTION_SYNONYMS.items():
            for s in synonyms:
                syn_map[s.lower()] = canonical

        sections: dict[str, list[str]] = {k: [] for k in _CANONICAL_SECTIONS}
        current = "Lead"  # implicit head until first ## header
        header_re = re.compile(r"^##\s+(.+?)\s*$")

        for line in md.splitlines():
            m = header_re.match(line)
            if m:
                key = syn_map.get(m.group(1).strip().lower())
                if key:
                    current = key
                    continue
                # Unknown ## header — keep it inside the current section.
            sections[current].append(line)

        return {k: "\n".join(v).strip() for k, v in sections.items()}

    @staticmethod
    def _diff_sections(
        old: dict[str, str], new: dict[str, str]
    ) -> dict[str, str]:
        """Tag each section as ``added``, ``edited``, ``removed``, or
        ``unchanged``. Lightweight — just a presence + equality check."""
        diff: dict[str, str] = {}
        keys = set(old.keys()) | set(new.keys())
        for k in keys:
            o = (old.get(k) or "").strip()
            n = (new.get(k) or "").strip()
            if not o and n:
                diff[k] = "added"
            elif o and not n:
                diff[k] = "removed"
            elif o != n:
                diff[k] = "edited"
            else:
                diff[k] = "unchanged"
        return diff

    @staticmethod
    def _jaccard_distance(a: str, b: str) -> float:
        """1.0 − Jaccard(a, b) over lowercased word sets. Cheap drift
        detector for the incremental-compile guard."""
        ta = set(a.lower().split())
        tb = set(b.lower().split())
        if not ta and not tb:
            return 0.0
        intersection = len(ta & tb)
        union = len(ta | tb) or 1
        return 1.0 - (intersection / union)

    @staticmethod
    def _target_lead_words(budget_tokens: int) -> int:
        """Crude mapping of total page budget → target words for the
        Lead paragraph. Roughly 25% of budget, capped at 120 words."""
        return min(120, max(40, int(budget_tokens * 0.75 // 4)))

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
