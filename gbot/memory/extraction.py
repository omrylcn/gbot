"""MemoryService — session summarization + fact extraction.

Single service that handles the full memory lifecycle at session close:
1. Summarize conversation (hybrid: narrative + structured bullets)
2. Extract typed facts (semantic, episodic, preference, procedural)
3. Save to memory_facts + user_notes (backward compat)
4. Log processing stats

Uses the 'memory' agent profile from agents.yaml for LLM prompts.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from loguru import logger

from gbot.agent.profiles import get_agent_md
from gbot.core.providers import litellm as llm_provider
from gbot.memory.store import MemoryStore


class MemoryService:
    """Unified memory processing: summarization + fact extraction.

    Uses agents.yaml 'memory' profile AGENT.md as system prompt.
    Both tasks share the same LLM context — the memory agent knows
    how to summarize and how to extract facts based on the user prompt.
    """

    def __init__(self, db: MemoryStore, model: str | None = None):
        self.db = db
        self.model = model or "openai/gpt-4o-mini"
        self._system_prompt = get_agent_md("memory") or ""

    async def process_session(
        self,
        user_id: str,
        messages: list[dict[str, Any]],
        session_id: str | None = None,
        channel: str | None = None,
    ) -> dict[str, Any]:
        """Full session close processing: summarize + extract facts.

        Returns dict with 'summary' and extraction stats.
        """
        result: dict[str, Any] = {"summary": "", "facts_extracted": 0, "facts_added": 0}

        # 1. Summarize
        try:
            result["summary"] = await self.summarize(messages)
        except Exception as e:
            logger.error(f"Summarization failed for session {session_id}: {e}")

        # 2. Extract facts
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

    # ── Fact Extraction ───────────────────────────────────────

    async def extract_and_save(
        self,
        user_id: str,
        messages: list[dict[str, Any]],
        session_id: str | None = None,
        channel: str | None = None,
        trigger: str = "session_close",
    ) -> dict[str, Any]:
        """Extract typed facts from messages and save to DB."""
        start = time.monotonic()

        raw_facts = await self._extract_typed_facts(messages)
        if not raw_facts:
            return {"facts_extracted": 0, "facts_added": 0}

        # Deduplicate against existing facts
        existing = self.db.get_facts(user_id, valid_only=True, limit=200)
        existing_contents = {f["content"].lower().strip() for f in existing}

        added = 0
        for fact in raw_facts:
            content = fact.get("content", "").strip()
            if not content or content.lower() in existing_contents:
                continue

            fact_id = str(uuid.uuid4())[:8]
            fact_type = fact.get("type", "semantic")
            if fact_type not in ("semantic", "episodic", "preference", "procedural"):
                fact_type = "semantic"

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
            )

            added += 1
            existing_contents.add(content.lower())

        duration_ms = int((time.monotonic() - start) * 1000)

        self.db.log_memory_processing(
            user_id=user_id,
            session_id=session_id,
            trigger=trigger,
            facts_extracted=len(raw_facts),
            facts_added=added,
            duration_ms=duration_ms,
        )

        return {
            "facts_extracted": len(raw_facts),
            "facts_added": added,
            "duration_ms": duration_ms,
        }

    async def _extract_typed_facts(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Call LLM with memory agent prompt to extract typed facts."""
        if not self._system_prompt:
            logger.warning("No memory agent prompt (agents.yaml memory profile)")
            return []

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
                max_tokens=500,
                response_format={"type": "json_object"},
            )
            raw = response.content or '{"facts": []}'
            data = json.loads(raw)
            facts = data.get("facts", [])
            return [f for f in facts if isinstance(f, dict) and f.get("content")]
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"Typed fact extraction failed: {e}")
            return []
