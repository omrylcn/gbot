"""MemoryService — session summarization + fact extraction + AUDN update.

Single service that handles the full memory lifecycle:
1. Summarize conversation (hybrid: narrative + structured bullets)
2. Extract typed facts (semantic, episodic, preference, procedural)
3. AUDN update: embed → search similar → LLM decides ADD/UPDATE/NOOP
4. Temporal user_notes: process → transfer to memory_facts → delete
5. Log processing stats

Uses the 'memory' agent profile from agents.yaml for LLM prompts.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from loguru import logger

from gbot.agent.profiles import get_agent_md
from gbot.core.config.schema import MemoryConfig
from gbot.core.providers import llm as llm_provider
from gbot.memory.embedder import MemoryEmbedder
from gbot.memory.store import MemoryStore


class MemoryService:
    """Unified memory processing: summarization + extraction + AUDN.

    Uses agents.yaml 'memory' profile AGENT.md as system prompt.
    Embedding via MemoryEmbedder (sync, OpenRouter API).
    """

    def __init__(
        self,
        db: MemoryStore,
        model: str | None = None,
        config: MemoryConfig | None = None,
        embedder: MemoryEmbedder | None = None,
        resolver=None,
        entity_compiler=None,
    ):
        """
        Parameters
        ----------
        resolver : EntityResolver | None
            Optional resolver used to canonicalize relation entities at
            insert time (Faz 22D). Falls back to raw surface forms when
            ``None`` — relations still save, just without normalization.
        entity_compiler : EntityPageCompiler | None
            Optional debounced compiler that schedules entity-page
            recompilation after touched extractions. ``None`` skips the
            entity-page lifecycle entirely.
        """
        self.db = db
        self.model = model or "openai/gpt-4o-mini"
        self.config = config
        self.embedder = embedder
        self.resolver = resolver
        self.entity_compiler = entity_compiler
        self._system_prompt = get_agent_md("memory") or ""
        update_model = config.update.model if config else ""
        self._update_model = update_model or self.model

    async def process_session(
        self,
        user_id: str,
        messages: list[dict[str, Any]],
        session_id: str | None = None,
        channel: str | None = None,
    ) -> dict[str, Any]:
        """Full session close processing: summarize + extract facts."""
        result: dict[str, Any] = {
            "summary": "", "facts_extracted": 0, "facts_added": 0, "facts_updated": 0,
        }

        try:
            result["summary"] = await self.summarize(messages)
        except Exception as e:
            logger.error(f"Summarization failed for session {session_id}: {e}")

        try:
            stats = await self.extract_and_save(
                user_id, messages,
                session_id=session_id,
                channel=channel,
                trigger="session_close",
            )
            result.update(stats)
        except Exception as e:
            logger.warning(f"Fact extraction failed for session {session_id}: {e}")

        return result

    # ── Summarization ─────────────────────────────────────────

    async def summarize(self, messages: list[dict[str, Any]]) -> str:
        """Summarize conversation using memory agent prompt."""
        if not self._system_prompt:
            return ""

        summary_messages = [
            {"role": "system", "content": self._system_prompt},
            *messages,
            {"role": "user", "content": "Summarize this conversation concisely."},
        ]
        try:
            response = await llm_provider.achat(
                summary_messages,
                model=self.model,
                temperature=0.3,
                max_tokens=500,
            )
            return response.content or ""
        except Exception as e:
            logger.error(f"Summarization failed: {e}")
            return ""

    # ── Fact Extraction + AUDN ────────────────────────────────

    async def extract_and_save(
        self,
        user_id: str,
        messages: list[dict[str, Any]],
        session_id: str | None = None,
        channel: str | None = None,
        trigger: str = "session_close",
    ) -> dict[str, Any]:
        """Extract typed facts, embed, AUDN update, save to DB."""
        start = time.monotonic()

        raw_facts, raw_relations = await self._extract_typed_facts(messages)

        # Save relations — with canonical entity resolution (Faz 22D).
        # The partial UNIQUE index on (user, source, relation, target)
        # collapses re-asserts; the ON CONFLICT path in add_relation
        # updates confidence/source_fact instead of inserting duplicates.
        for rel in raw_relations:
            src = rel.get("source", "")
            tgt = rel.get("target", "")
            cs = self.resolver.canonicalize(user_id, src) if self.resolver else None
            ct = self.resolver.canonicalize(user_id, tgt) if self.resolver else None
            try:
                self.db.add_relation(
                    relation_id=str(uuid.uuid4())[:8],
                    user_id=user_id,
                    source_entity=src,
                    relation=rel.get("relation", ""),
                    target_entity=tgt,
                    canonical_source=cs,
                    canonical_target=ct,
                )
            except Exception as e:
                logger.debug(f"add_relation skipped: {e}")

        if not raw_facts:
            return {"facts_extracted": 0, "facts_added": 0, "facts_updated": 0}

        added = 0
        updated = 0
        deleted = 0

        for fact in raw_facts:
            content = fact.get("content", "").strip()
            if not content:
                continue

            fact_type = fact.get("type", "semantic")
            # Faz 22G adds 'style' — kullanıcının iletişim tonu / dil tercihi.
            if fact_type not in (
                "semantic", "episodic", "preference", "procedural", "style",
            ):
                fact_type = "semantic"

            # Embed the new fact
            embedding = None
            if self.embedder:
                embedding = self.embedder.embed(content)

            # Search similar existing facts
            similar = []
            if self.embedder and embedding:
                similar = self.db.search_similar_facts(user_id, embedding, top_k=5)

            # AUDN decision
            if similar:
                decision = await self._audn_decide(fact, similar)

                if decision["action"] == "noop":
                    continue
                elif decision["action"] == "update":
                    target_id = decision.get("target_fact_id")
                    if target_id:
                        new_fact_id = str(uuid.uuid4())[:8]
                        self.db.invalidate_fact(target_id, superseded_by=new_fact_id)
                        self.db.add_fact(
                            fact_id=new_fact_id,
                            user_id=user_id,
                            content=content,
                            fact_type=fact_type,
                            source="extraction",
                            source_session=session_id,
                            source_channel=channel,
                            confidence=float(fact.get("confidence", 0.8)),
                            importance=float(fact.get("importance", 0.5)),
                            keywords=fact.get("keywords"),
                            category=fact.get("category"),
                            embedding=embedding,
                        )
                        updated += 1
                        logger.debug(f"AUDN UPDATE: '{target_id}' → '{new_fact_id}'")
                    continue
                elif decision["action"] == "delete":
                    target_id = decision.get("target_fact_id")
                    if target_id:
                        self.db.invalidate_fact(target_id)
                        deleted += 1
                        logger.debug(f"AUDN DELETE: '{target_id}'")
                    continue
                # action == "add" → fall through

            # ADD new fact
            fact_id = str(uuid.uuid4())[:8]
            self.db.add_fact(
                fact_id=fact_id,
                user_id=user_id,
                content=content,
                fact_type=fact_type,
                source="extraction",
                source_session=session_id,
                source_channel=channel,
                confidence=float(fact.get("confidence", 0.8)),
                importance=float(fact.get("importance", 0.5)),
                keywords=fact.get("keywords"),
                category=fact.get("category"),
                embedding=embedding,
            )
            added += 1

        duration_ms = int((time.monotonic() - start) * 1000)

        self.db.log_memory_processing(
            user_id=user_id,
            session_id=session_id,
            trigger=trigger,
            facts_extracted=len(raw_facts),
            facts_added=added,
            facts_updated=updated,
            facts_invalidated=deleted,
            duration_ms=duration_ms,
        )

        stats = {
            "facts_extracted": len(raw_facts),
            "facts_added": added,
            "facts_updated": updated,
            "facts_deleted": deleted,
            "duration_ms": duration_ms,
        }
        if added or updated:
            logger.info(f"Memory extraction for {user_id}: {stats}")

        # Faz 22D — entity page recompilation (debounced).
        # Faz 22J — extended ripple: scan fact content for known canonicals
        # so a single new fact can touch multiple pages (Karpathy "10-15"
        # pattern) without an explicit relation tying them.
        if (added or updated or deleted) and self.entity_compiler:
            touched = self._collect_touched_entities(
                user_id, raw_relations, raw_facts,
            )
            for canonical in touched:
                try:
                    await self.entity_compiler.enqueue(user_id, canonical)
                except Exception as e:
                    logger.debug(f"entity_compiler enqueue failed: {e}")

        return stats

    def _collect_touched_entities(
        self,
        user_id: str,
        raw_relations: list[dict[str, Any]],
        raw_facts: list[dict[str, Any]] | None = None,
    ) -> set[str]:
        """Canonical entity names mentioned by the latest extraction.

        Two sources combined:
        1. Relations — every source/target canonical (always).
        2. **Faz 22J ripple**: scan fact ``content`` against the known
           canonical pool from ``memory_entity_pages``; a fact that
           mentions "Zeynep" causes the Zeynep page to recompile even
           if the extracted relations don't reference her explicitly.

        Capped at ``memory.entity_pages.max_ripple_per_extraction`` so a
        long article touching every canonical doesn't trigger a compile
        storm. Sorted by mention strength (relations first, then richer
        canonicals).
        """
        touched: set[str] = set()
        # 1. Relations (legacy path) — always counted, no cap.
        for rel in raw_relations:
            for surface in (rel.get("source"), rel.get("target")):
                if not surface:
                    continue
                if self.resolver:
                    canonical = self.resolver.canonicalize(user_id, surface)
                else:
                    canonical = surface
                if canonical:
                    touched.add(canonical)

        # 2. Fact-content ripple.
        page_cfg = getattr(self.config, "entity_pages", None) if self.config else None
        ripple_cap = (
            getattr(page_cfg, "max_ripple_per_extraction", 12) if page_cfg else 12
        )
        if raw_facts and ripple_cap > 0:
            try:
                known = self.db.list_canonical_entities(user_id) or []
            except Exception:
                known = []
            # Substring scan — case-insensitive, longest-first so "Akasya
            # AVM" wins over "Akasya". Skip entities already touched via
            # relations to save work.
            for canonical in sorted(known, key=len, reverse=True):
                if not canonical or canonical in touched:
                    continue
                if len(touched) >= ripple_cap:
                    break
                hay = canonical.lower()
                for fact in raw_facts:
                    body = (fact.get("content") or "").lower()
                    if hay in body:
                        touched.add(canonical)
                        break

        return touched

    async def _audn_decide(
        self, new_fact: dict, similar_facts: list[dict]
    ) -> dict[str, Any]:
        """Ask LLM: ADD, UPDATE, or NOOP for a new fact vs existing similar facts."""
        existing_str = "\n".join(
            f"- [{f['fact_id']}] {f['content']}" for f in similar_facts
        )
        new_str = new_fact.get("content", "")

        prompt = (
            f"EXISTING facts:\n{existing_str}\n\n"
            f"NEW fact: {new_str}\n\n"
            "Compare and decide: ADD, UPDATE, or NOOP?"
        )

        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": prompt},
        ]

        try:
            response = await llm_provider.achat(
                messages,
                model=self._update_model,
                temperature=0.1,
                max_tokens=200,
                response_format={"type": "json_object"},
            )
            raw = response.content or '{"action": "add"}'
            decision = json.loads(raw)
            action = decision.get("action", "add").lower()
            if action not in ("add", "update", "delete", "noop"):
                action = "add"
            return {
                "action": action,
                "target_fact_id": decision.get("target_fact_id"),
                "reason": decision.get("reason", ""),
            }
        except Exception as e:
            logger.warning(f"AUDN decision failed: {e} — defaulting to ADD")
            return {"action": "add", "target_fact_id": None, "reason": "error"}

    # ── Temporal user_notes processing ────────────────────────

    async def process_temporal_notes(self, user_id: str) -> int:
        """Transfer user_notes to memory_facts, then delete all processed notes.

        Every note is deleted after processing — success or fail.
        Notes are temporal buffers, not permanent storage.
        """
        notes = self.db.get_notes_with_ids(user_id, limit=50)
        if not notes:
            return 0

        processed = 0
        for note in notes:
            content = note["content"].strip()
            if not content:
                self.db.delete_note(note["id"])
                continue

            try:
                embedding = None
                if self.embedder:
                    embedding = self.embedder.embed(content)

                similar = []
                if self.embedder and embedding:
                    similar = self.db.search_similar_facts(user_id, embedding, top_k=3)

                if similar:
                    decision = await self._audn_decide(
                        {"content": content}, similar
                    )
                    if decision["action"] == "update":
                        target_id = decision.get("target_fact_id")
                        if target_id:
                            new_id = str(uuid.uuid4())[:8]
                            self.db.invalidate_fact(target_id, superseded_by=new_id)
                            self.db.add_fact(
                                fact_id=new_id, user_id=user_id, content=content,
                                fact_type="semantic", source="note_transfer",
                                embedding=embedding,
                            )
                    elif decision["action"] == "delete":
                        target_id = decision.get("target_fact_id")
                        if target_id:
                            self.db.invalidate_fact(target_id)
                    elif decision["action"] == "add":
                        fact_id = str(uuid.uuid4())[:8]
                        self.db.add_fact(
                            fact_id=fact_id, user_id=user_id, content=content,
                            fact_type="semantic", source="note_transfer",
                            embedding=embedding,
                        )
                    # noop → just delete note
                else:
                    # No similar facts → ADD
                    fact_id = str(uuid.uuid4())[:8]
                    self.db.add_fact(
                        fact_id=fact_id, user_id=user_id, content=content,
                        fact_type="semantic", source="note_transfer",
                        embedding=embedding,
                    )
            except Exception as e:
                logger.warning(f"Temporal note processing failed: {e}")

            # Always delete — note is temporal, don't let it accumulate
            self.db.delete_note(note["id"])
            processed += 1

        if processed:
            logger.info(f"Temporal notes processed for {user_id}: {processed}")
        return processed

    # ── LLM extraction ────────────────────────────────────────

    async def _extract_typed_facts(
        self, messages: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Call LLM to extract typed facts + relations.

        Returns (facts, relations) tuple.
        """
        if not self._system_prompt:
            logger.warning("No memory agent prompt (agents.yaml memory profile)")
            return [], []

        extraction_messages = [
            {"role": "system", "content": self._system_prompt},
            *messages,
            {"role": "user", "content": "Extract typed facts from this conversation as JSON."},
        ]

        try:
            response = await llm_provider.achat(
                extraction_messages,
                model=self.model,
                temperature=0.1,
                max_tokens=1000,
                response_format={"type": "json_object"},
            )
            raw = response.content or '{"facts": []}'
            data = json.loads(raw)
            if isinstance(data, list):
                data = data[0] if data else {}
            if not isinstance(data, dict):
                return [], []
            facts = data.get("facts", [])
            relations = data.get("relations", [])
            valid_facts = [f for f in facts if isinstance(f, dict) and f.get("content")]
            valid_rels = [r for r in relations if isinstance(r, dict)
                         and r.get("source") and r.get("relation") and r.get("target")]
            return valid_facts, valid_rels
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"Typed fact extraction failed: {e}")
            return [], []
