"""ContextBuilder — assembles system prompt from SQLite + workspace files."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from gbot.agent.context.models import LayerResult
from gbot.agent.profiles import get_agent_md, get_agent_skills
from gbot.agent.skills.loader import SkillLoader
from gbot.core.config.schema import Config
from gbot.memory.store import MemoryStore


class ContextBuilder:
    """Builds layered system prompt with configurable persona, roles, and token budgets.

    Layers:
      1. Identity (prompt_template > system_prompt > AGENT.md > persona config)
      2. Runtime info (user_id, datetime)
      3. Current role (if configured)
      4. Agent memory (agent_memory table)
      5. User context (notes, favorites, preferences)
      6. Previous session summary
      7. Skills (always-on + index)
    """

    def __init__(
        self, config: Config, db: MemoryStore, profile: str = "main",
        embedder=None,
    ):
        self.config = config
        self.db = db
        self.profile = profile
        self.embedder = embedder  # MemoryEmbedder (optional, for semantic retrieval)
        self.skills = SkillLoader(
            workspace=config.workspace_path,
            builtin_dir=Path(__file__).parent.parent / "skills" / "builtin",
        )

    def build_layers(
        self,
        user_id: str,
        role: str | None = None,
        context_layers: set[str] | None = None,
        mark_delivered: bool = False,
        template_vars: dict[str, str] | None = None,
        last_message: str | None = None,
    ) -> dict[str, LayerResult]:
        """Build each context layer individually.

        Parameters
        ----------
        user_id : str
            Target user ID.
        role : str, optional
            Override role name.
        context_layers : set[str], optional
            Allowed layers from RBAC. None = all.
        mark_delivered : bool
            If True, mark events as delivered (side-effect).
            Use False for preview/inspection.

        Returns
        -------
        dict[str, LayerResult]
            Ordered dict of layer_name -> LayerResult.
        """
        priorities = self.config.assistant.context_priorities
        layers: dict[str, LayerResult] = {}

        def _allowed(layer: str) -> bool:
            return context_layers is None or layer in context_layers

        # Layer metadata: description and source
        _LAYER_META: dict[str, tuple[str, str]] = {
            "identity": ("Bot identity, persona and rules", "workspace/AGENT.md or persona config"),
            "runtime": ("Current user, datetime, model info", "Config + datetime.now()"),
            "role": ("RBAC role description (guest only)", "config/roles.yaml"),
            "agent_memory": ("Long-term facts the bot remembers", "agent_memory table (SQLite)"),
            "user_context": ("Notes, preferences, favorites", "user_notes + preferences + favorites (SQLite)"),
            "session_summary": ("Previous session LLM summary", "sessions table (SQLite)"),
            "skills": ("Always-on skill content", "SKILL.md files (workspace + builtin)"),
            "skills_index": ("Available skills for load_skill()", "SKILL.md files (workspace + builtin)"),
        }

        def _add(name: str, content: str, budget: int = 0) -> None:
            truncated_content = self._truncate(content, budget) if budget else content
            was_truncated = len(truncated_content) < len(content)
            desc, src = _LAYER_META.get(name, ("", ""))
            layers[name] = LayerResult(
                name=name,
                description=desc,
                source=src,
                content=truncated_content,
                chars=len(truncated_content),
                tokens=len(truncated_content) // 4,
                budget=budget,
                truncated=was_truncated,
                enabled=True,
            )

        def _add_empty(name: str) -> None:
            desc, src = _LAYER_META.get(name, ("", ""))
            layers[name] = LayerResult(
                name=name, description=desc, source=src,
                content="", chars=0, tokens=0,
                budget=0, truncated=False, enabled=True,
            )

        # 1. Identity
        if _allowed("identity"):
            identity = self._get_identity()
            if identity and template_vars:
                try:
                    identity = identity.format(**template_vars)
                except (KeyError, ValueError):
                    pass  # template vars not found, use as-is
            if identity:
                _add("identity", identity, priorities.identity)

        # 2. Runtime info
        if _allowed("runtime"):
            now_dt = datetime.now()
            now = now_dt.strftime("%Y-%m-%d %H:%M")
            # Faz 22H — Turkish day-of-week + last-activity gap so the LLM
            # has a sharper "şu an" than just an ISO timestamp.
            day_names = [
                "Pazartesi", "Salı", "Çarşamba", "Perşembe",
                "Cuma", "Cumartesi", "Pazar",
            ]
            month_names = [
                "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
                "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
            ]
            today_human = (
                f"{day_names[now_dt.weekday()]}, "
                f"{now_dt.day} {month_names[now_dt.month - 1]} "
                f"{now_dt.year}, {now_dt.strftime('%H:%M')}"
            )

            # Last activity hint — use the most recent closed session's
            # ended_at when available; falls back to silent omission.
            last_activity_line = ""
            try:
                meta = (
                    self.db.get_last_session_meta(user_id)
                    if hasattr(self.db, "get_last_session_meta")
                    else None
                )
                ended = (meta or {}).get("ended_at")
                if ended:
                    last_activity_line = (
                        f"- Bu kullanıcıyla son aktivite: "
                        f"{self._relative_age(ended)}\n"
                    )
            except Exception:  # pragma: no cover — defensive
                last_activity_line = ""

            user = self.db.get_user(user_id)
            user_name = user["name"] if user and user.get("name") else user_id
            _add("runtime", (
                f"# Runtime\n\n"
                f"- Current user_id: {user_id}\n"
                f"- Current user_name: {user_name}\n"
                f"- Current time: {now}\n"
                f"- Bugün: {today_human}\n"
                f"{last_activity_line}"
                f"- Use this user_id when calling tools that require it."
            ))

        # 3. Current role
        if _allowed("role"):
            role_text = self._get_role(role)
            if role_text:
                _add("role", f"# Current Role\n\n{role_text}")
            else:
                _add_empty("role")

        # 4. Agent memory
        if _allowed("agent_memory"):
            memory = self.db.read_memory("long_term")
            if memory:
                _add("agent_memory", f"# Agent Memory\n\n{memory}", priorities.agent_memory)
            else:
                _add_empty("agent_memory")

        # 5. User context (explicit notes + learned facts)
        if _allowed("user_context"):
            # Explicit: notes, preferences, favorites
            user_ctx = self.db.get_user_context(user_id)

            # Learned: memory_facts (2-stage retrieval: search → re-rank)
            learned = ""
            try:
                if self.embedder and last_message:
                    query_emb = self.embedder.embed(last_message)
                    retrieval_cfg = self.config.memory.retrieval
                    # Stage 1: broad candidates (similarity + distance gate).
                    # The gate is applied inside the SQL CTE so the rerank
                    # pool never sees distant-but-resurrectable noise.
                    pool_size = retrieval_cfg.top_k_candidates
                    if (
                        retrieval_cfg.llm_rerank.enabled
                        and retrieval_cfg.llm_rerank.candidates_pool > pool_size
                    ):
                        pool_size = retrieval_cfg.llm_rerank.candidates_pool
                    candidates = self.db.search_similar_facts(
                        user_id,
                        query_emb,
                        top_k=pool_size,
                        max_distance=retrieval_cfg.max_distance,
                    )
                    # Stage 2: re-rank.
                    if retrieval_cfg.llm_rerank.enabled and candidates:
                        facts = self._llm_rerank(
                            candidates,
                            query=last_message,
                            top_k=retrieval_cfg.top_k_final,
                            llm_cfg=retrieval_cfg.llm_rerank,
                            fallback_top_k=retrieval_cfg.top_k_final,
                        )
                    else:
                        facts = self._rerank_facts(
                            candidates, top_k=retrieval_cfg.top_k_final
                        )
                else:
                    facts = self.db.get_facts(user_id, valid_only=True, limit=15)
                if facts:
                    # Faz 22H — every fact carries its age so the LLM can
                    # judge whether a 12-day-old intent ("X yapacağım")
                    # is still in play.
                    lines = "\n".join(
                        f"- ({self._relative_age(f.get('created_at'))}) "
                        f"{f['content']}"
                        for f in facts
                    )
                    learned = f"LEARNED FACTS:\n{lines}"
                    # Access tracking — batch update
                    fact_ids = [f["fact_id"] for f in facts]
                    self.db.batch_increment_access(fact_ids)
            except Exception:
                pass  # memory_facts/vec table may not exist in tests

            # Faz 22D — Backlinks: detect canonical entities mentioned in
            # retrieved facts, inject their relations as a RELATIONSHIPS block.
            relationships = ""
            entity_pages_block = ""
            entities_in_play: list[str] = []
            try:
                if self.config.memory.relations.enabled and facts:
                    entities_in_play = self._detect_canonical_entities(user_id, facts)
                    relationships = self._render_relationships_block(
                        user_id, entities_in_play
                    )
            except Exception:  # pragma: no cover — defensive
                pass

            # Faz 22D — Entity pages: when a compiled page exists for one
            # of the entities in play, inject the page instead of (or in
            # addition to) the raw relations list. Pages compile on a
            # debounced schedule by EntityPageCompiler; here we just read.
            try:
                if (
                    self.config.memory.entity_pages.enabled
                    and entities_in_play
                ):
                    entity_pages_block = self._render_entity_pages_block(
                        user_id, entities_in_play
                    )
            except Exception:  # pragma: no cover — defensive
                pass

            # Faz 22G — Style facts: how the user prefers to communicate.
            # Pulled separately from semantic/episodic facts so they're
            # *always* present (not subject to query-similarity gating)
            # and clearly framed for the LLM.
            style_block = ""
            try:
                style_facts = self.db.get_facts(
                    user_id, fact_type="style", valid_only=True, limit=8
                )
                if style_facts:
                    style_lines = "\n".join(
                        f"- {f['content']}" for f in style_facts
                    )
                    style_block = f"USER STYLE:\n{style_lines}"
            except Exception:  # pragma: no cover — defensive
                pass

            combined = "\n\n".join(
                p
                for p in [
                    user_ctx,
                    style_block,
                    learned,
                    relationships,
                    entity_pages_block,
                ]
                if p
            )
            if combined:
                _add("user_context", f"# User Context\n\n{combined}", priorities.user_context)
            else:
                _add_empty("user_context")

        # 6. Previous session summary
        if _allowed("session_summary"):
            # Faz 22H — header carries the gap so the LLM knows whether
            # the last conversation was minutes or months ago.
            meta = None
            if hasattr(self.db, "get_last_session_meta"):
                try:
                    meta = self.db.get_last_session_meta(user_id)
                except Exception:  # pragma: no cover — defensive
                    meta = None
            summary = (meta or {}).get("summary") if meta else (
                self.db.get_last_session_summary(user_id)
            )
            if summary:
                age_tag = ""
                if meta and meta.get("ended_at"):
                    age_tag = f" ({self._relative_age(meta['ended_at'])})"
                _add(
                    "session_summary",
                    f"# Previous Conversation{age_tag}\n\n{summary}",
                    priorities.session_summary,
                )
            else:
                _add_empty("session_summary")

        # 8. Skills
        if _allowed("skills"):
            profile_skills = get_agent_skills(self.profile)
            if profile_skills != []:
                always_on = self.skills.get_always_on()
                if profile_skills != ["*"] and profile_skills:
                    allowed_names = set(profile_skills)
                    always_on = [s for s in always_on if s.name in allowed_names]
                if always_on:
                    skill_texts = [self.skills.load_content(s.name) for s in always_on]
                    active = "\n\n---\n\n".join(t for t in skill_texts if t)
                    if active:
                        _add("skills", f"# Active Skills\n\n{active}", priorities.skills)

                index = self.skills.build_index()
                if index:
                    _add("skills_index", (
                        "# Available Skills\n\n"
                        "Use load_skill(skill_name) to load detailed instructions when needed.\n\n"
                        + index
                    ))

        return layers

    def build(
        self,
        user_id: str,
        role: str | None = None,
        context_layers: set[str] | None = None,
        last_message: str | None = None,
    ) -> str:
        """Build full system prompt for a user (backward compatible).

        Parameters
        ----------
        user_id : str
            Target user ID.
        role : str, optional
            Override role name. Falls back to config default.
        context_layers : set[str], optional
            Allowed context layers from RBAC. None = all layers.
        last_message : str, optional
            Last user message for semantic memory retrieval.
        """
        layers = self.build_layers(
            user_id, role, context_layers, mark_delivered=True,
            last_message=last_message,
        )
        parts = [lr.content for lr in layers.values() if lr.content]
        return "\n\n---\n\n".join(parts)

    def get_context_stats(
        self,
        user_id: str,
        role: str | None = None,
        context_layers: set[str] | None = None,
    ) -> dict:
        """Measure each context layer's size without building the full prompt.

        Returns a dict with per-layer char/token counts and totals.
        """
        layers = self.build_layers(user_id, role, context_layers, mark_delivered=False)
        layer_stats = [
            {
                "layer": lr.name,
                "chars": lr.chars,
                "tokens": lr.tokens,
                "budget": lr.budget,
                "truncated": lr.truncated,
            }
            for lr in layers.values()
        ]
        return {
            "layers": layer_stats,
            "total_chars": sum(d["chars"] for d in layer_stats),
            "total_tokens": sum(d["tokens"] for d in layer_stats),
        }

    # ── Identity resolution ───────────────────────────────────

    def _get_identity(self) -> str:
        """Get identity prompt.

        Priority: prompt_template > system_prompt > profile AGENT.md > workspace/AGENT.md > persona config.
        """
        # Priority 0: custom prompt template file
        template = self._load_template()
        if template:
            return self._apply_persona_suffix(template)

        # Priority 1: explicit system_prompt in config
        if self.config.assistant.system_prompt:
            return self._apply_persona_suffix(self.config.assistant.system_prompt)

        # Priority 2: profile AGENT.md (from agents.yaml)
        profile_md = get_agent_md(self.profile)
        if profile_md and profile_md.strip():
            return self._apply_persona_suffix(profile_md.strip())

        # Priority 3: workspace/AGENT.md (direct fallback)
        agent_md = self.config.workspace_path / "AGENT.md"
        if agent_md.exists():
            content = agent_md.read_text(encoding="utf-8").strip()
            if content:
                return self._apply_persona_suffix(content)

        # Priority 4: build from persona config
        return self._build_persona_prompt()

    def _build_persona_prompt(self) -> str:
        """Build identity from persona config (fallback when no AGENT.md)."""
        persona = self.config.assistant.persona
        name = persona.name or self.config.assistant.name
        parts = [f"You are {name}, a helpful AI assistant."]
        if persona.tone:
            parts.append(f"Tone: {persona.tone}.")
        if persona.language:
            parts.append(f"Always respond in: {persona.language}.")
        if persona.constraints:
            parts.append("Constraints:")
            for c in persona.constraints:
                parts.append(f"- {c}")
        return "\n".join(parts)

    def _apply_persona_suffix(self, base: str) -> str:
        """Append persona constraints to existing identity if configured."""
        persona = self.config.assistant.persona
        if not persona.constraints:
            return base
        suffix = "\n\n## Additional Constraints\n\n" + "\n".join(
            f"- {c}" for c in persona.constraints
        )
        return base + suffix

    def _load_template(self) -> str | None:
        """Load custom prompt template file if configured."""
        path = self.config.assistant.prompt_template
        if not path:
            return None
        template_path = Path(path).expanduser().resolve()
        if not template_path.exists():
            return None
        raw = template_path.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        persona = self.config.assistant.persona
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        variables = {
            "name": persona.name or self.config.assistant.name,
            "tone": persona.tone,
            "language": persona.language,
            "datetime": now,
        }
        try:
            return raw.format_map(variables)
        except (KeyError, ValueError):
            return raw

    # ── Role resolution ───────────────────────────────────────

    def _get_role(self, role_name: str | None = None) -> str | None:
        """Get role description. Falls back to config default role."""
        roles = self.config.assistant.roles
        name = role_name or roles.default
        if not name:
            return None
        if name in roles.available:
            return f"Role: {name} — {roles.available[name]}"
        if roles.default:
            return f"Role: {roles.default}"
        return None

    # ── Helpers ───────────────────────────────────────────────

    @staticmethod
    def _relative_age(timestamp: str | None) -> str:
        """Human-readable Turkish age tag for fact / summary rendering.

        ``"2 gün önce"``, ``"3 hafta önce"``, ``"5 ay önce"``. Falls back
        to ``"yakın zaman"`` on parse error so the LLM never sees an
        empty tag.
        """
        if not timestamp:
            return "yakın zaman"
        try:
            then = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            if then.tzinfo:
                then = then.replace(tzinfo=None)
            delta_seconds = (datetime.now() - then).total_seconds()
        except (ValueError, TypeError):
            return "yakın zaman"

        if delta_seconds < 60:
            return "az önce"
        minutes = delta_seconds / 60
        if minutes < 60:
            return f"{int(minutes)} dakika önce"
        hours = minutes / 60
        if hours < 24:
            return f"{int(hours)} saat önce"
        days = hours / 24
        if days < 1:
            return "bugün"
        if days < 14:
            return f"{int(days)} gün önce"
        if days < 60:
            return f"{int(days // 7)} hafta önce"
        if days < 365:
            return f"{int(days // 30)} ay önce"
        return f"{int(days // 365)} yıl önce"

    @staticmethod
    def _rerank_facts(
        candidates: list[dict], top_k: int = 10
    ) -> list[dict]:
        """Re-rank facts by similarity × retrieval_strength.

        retrieval_strength = recency × access_factor × confidence
        """
        from datetime import datetime

        now = datetime.now()
        for f in candidates:
            try:
                created = datetime.fromisoformat(f.get("created_at", ""))
                days_old = (now - created).days
            except (ValueError, TypeError):
                days_old = 0
            recency = max(0.1, 1.0 - (days_old / 365))
            access = min(1.0, (f.get("access_count", 0) or 0) / 10)
            confidence = f.get("confidence", 1.0) or 1.0
            strength = recency * (0.3 + 0.7 * access) * confidence
            similarity = max(0.0, 1.0 - (f.get("distance", 1.0) or 1.0))
            f["final_score"] = similarity * strength

        candidates.sort(key=lambda f: f["final_score"], reverse=True)
        return candidates[:top_k]

    def _llm_rerank(
        self,
        candidates: list[dict],
        query: str,
        top_k: int,
        llm_cfg: Any,
        fallback_top_k: int | None = None,
    ) -> list[dict]:
        """Faz 22G Aşama 5 — Engram-style read-time deep scoring.

        Asks an LLM to re-order the candidate pool against the actual
        query text instead of the static multiplicative formula. The
        LLM call is an extra hot-path latency hit; opt in via
        ``memory.retrieval.llm_rerank.enabled``.

        Always falls back to ``_rerank_facts`` on any error so a flaky
        provider can never break retrieval. Async LLM call runs in a
        worker thread so the surrounding sync ContextBuilder stays
        compatible.
        """
        import asyncio
        import concurrent.futures
        import json

        fallback = fallback_top_k or top_k
        if not candidates:
            return []
        # Cap candidates for the prompt so token cost stays bounded.
        prompt_pool = candidates[: max(top_k, llm_cfg.candidates_pool)]
        listing = "\n".join(
            f"[{i}] {(c.get('content') or '')[:240]}"
            for i, c in enumerate(prompt_pool)
        )
        n = min(top_k, len(prompt_pool))
        prompt = (
            "Bu sorgu için en alakalı {n} fact'i seç ve sırala. Tam "
            "JSON formatında dön: {{\"ranked\": [<index>, ...]}}\n\n"
            "QUERY:\n{query}\n\n"
            "CANDIDATES:\n{listing}"
        ).format(n=n, query=query, listing=listing)

        from gbot.core.providers import litellm as llm_provider

        model = llm_cfg.model or self.config.memory.model

        async def _call():
            return await llm_provider.achat(
                messages=[{"role": "user", "content": prompt}],
                model=model,
                temperature=llm_cfg.temperature,
                max_tokens=llm_cfg.max_output_tokens,
                response_format={"type": "json_object"},
            )

        # Drive the async call. If we're inside a running event loop
        # (typical FastAPI handler), use a thread to avoid nested-loop
        # errors. Otherwise asyncio.run is fine.
        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                with concurrent.futures.ThreadPoolExecutor() as ex:
                    response = ex.submit(asyncio.run, _call()).result()
            else:
                response = asyncio.run(_call())
            text = (response.content or "").strip()
            if not text:
                extras = getattr(response, "additional_kwargs", {}) or {}
                text = (extras.get("reasoning_content") or "").strip()
            data = json.loads(text)
            ranked_indices = [
                int(i) for i in (data.get("ranked") or [])
                if isinstance(i, (int, float, str))
                and str(i).strip().lstrip("-").isdigit()
            ]
        except Exception as e:
            logger.warning(f"llm_rerank failed, falling back to static: {e}")
            return self._rerank_facts(candidates, top_k=fallback)

        if not ranked_indices:
            return self._rerank_facts(candidates, top_k=fallback)

        # Resolve indices to candidate rows; ignore out-of-range.
        seen: set[int] = set()
        ordered: list[dict] = []
        for idx in ranked_indices:
            if 0 <= idx < len(prompt_pool) and idx not in seen:
                seen.add(idx)
                ordered.append(prompt_pool[idx])
            if len(ordered) >= top_k:
                break
        # Fill any remainder from the static-formula tail to keep
        # top_k stable when the model returns fewer indices than asked.
        if len(ordered) < top_k:
            tail = self._rerank_facts(
                [c for i, c in enumerate(prompt_pool) if i not in seen],
                top_k=top_k - len(ordered),
            )
            ordered.extend(tail)
        return ordered[:top_k]

    def _detect_canonical_entities(
        self, user_id: str, facts: list[dict]
    ) -> list[str]:
        """Top-N canonical entities mentioned in retrieved facts.

        Cheap substring scan over the user's known canonical set. No NER,
        no LLM. Returned in descending mention-count order; ties broken
        by descending name length so multi-word entities outrank
        substring overlaps.
        """
        if not facts:
            return []

        rel_cfg = self.config.memory.relations
        max_entities = max(1, rel_cfg.max_entities_per_turn)

        with self.db._get_conn() as conn:
            rows = conn.execute(
                """SELECT DISTINCT canonical_source AS ent FROM memory_relations
                       WHERE user_id = ? AND canonical_source IS NOT NULL
                          AND canonical_source != ''
                   UNION
                   SELECT DISTINCT canonical_target FROM memory_relations
                       WHERE user_id = ? AND canonical_target IS NOT NULL
                          AND canonical_target != ''""",
                (user_id, user_id),
            ).fetchall()
        canonical_set = {r["ent"] for r in rows}
        if not canonical_set:
            return []

        sorted_canonicals = sorted(canonical_set, key=len, reverse=True)
        scores: dict[str, int] = {}
        for f in facts:
            content = (f.get("content") or "").lower()
            if not content:
                continue
            for ent in sorted_canonicals:
                if ent.lower() in content:
                    scores[ent] = scores.get(ent, 0) + 1

        if not scores:
            return []
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], -len(kv[0])))
        return [ent for ent, _ in ranked[:max_entities]]

    def _render_relationships_block(
        self, user_id: str, entities: list[str]
    ) -> str:
        """Render a RELATIONSHIPS section listing direct relations of the
        given canonical entities. Used together with (or as fallback for)
        the entity-pages block."""
        if not entities:
            return ""
        rel_cfg = self.config.memory.relations
        max_per_entity = max(1, rel_cfg.max_relations_per_entity)

        lines: list[str] = []
        for ent in entities:
            rels = self.db.get_relations(user_id, canonical=ent, limit=max_per_entity)
            if not rels:
                continue
            ent_lines: list[str] = []
            seen: set[tuple[str, str, str]] = set()
            for r in rels:
                src = r.get("canonical_source") or r.get("source_entity") or ""
                rel = r.get("relation") or ""
                tgt = r.get("canonical_target") or r.get("target_entity") or ""
                key = (src, rel, tgt)
                if key in seen:
                    continue
                seen.add(key)
                ent_lines.append(f"  - {src} → {rel} → {tgt}")
                if len(ent_lines) >= max_per_entity:
                    break
            if ent_lines:
                lines.append(f"**{ent}**:")
                lines.extend(ent_lines)

        if not lines:
            return ""
        return "RELATIONSHIPS:\n" + "\n".join(lines)

    def _render_entity_pages_block(
        self, user_id: str, entities: list[str]
    ) -> str:
        """Render an ENTITY PAGES section with compiled markdown summaries.

        For each entity in the in-play list, fetch its
        ``memory_entity_pages`` row. Pages are inserted as
        ``## {Entity}\\n{markdown}``. Stale pages still surface (with a
        marker) so the agent has *something* while the compiler catches
        up; the compiler is debounced and may take ~60s after a write.
        """
        if not entities:
            return ""
        page_cfg = self.config.memory.entity_pages
        max_pages = max(1, page_cfg.max_pages_in_context)

        sections: list[str] = []
        for ent in entities[:max_pages]:
            page = self.db.get_entity_page(user_id, ent)
            if not page:
                continue
            stale = " *(stale — recompile pending)*" if page.get("stale") else ""
            sections.append(f"## {ent}{stale}\n{page['content_md']}")
        if not sections:
            return ""
        return "ENTITY PAGES:\n" + "\n\n".join(sections)

    @staticmethod
    def _truncate(text: str, token_budget: int) -> str:
        """Truncate text to approximate token budget (1 token ~ 4 chars)."""
        char_limit = token_budget * 4
        if len(text) <= char_limit:
            return text
        return text[:char_limit] + "\n\n[...truncated]"
