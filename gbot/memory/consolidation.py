"""MemoryConsolidator — event-driven memory maintenance.

Triggered after extraction adds new facts (not timer-based):
1. Fact merge: overlapping facts → single rich fact
2. Decay: reduce importance of old, rarely-accessed facts

Called by MemoryService.extract_and_save() when facts_added > 0.
"""

from __future__ import annotations

import math
import uuid
from typing import Any

from loguru import logger

from gbot.agent.profiles import get_agent_md
from gbot.core.providers import litellm as llm_provider
from gbot.memory.embedder import MemoryEmbedder
from gbot.memory.store import MemoryStore


class MemoryConsolidator:
    """Event-driven memory maintenance — merge + decay."""

    def __init__(
        self,
        db: MemoryStore,
        embedder: MemoryEmbedder | None = None,
        model: str | None = None,
    ):
        self.db = db
        self.embedder = embedder
        self.model = model or "openai/gpt-4o-mini"
        self._system_prompt = get_agent_md("memory") or ""

    async def run(self, user_id: str) -> dict[str, Any]:
        """Run consolidation for a user. Called after new facts added."""
        stats: dict[str, Any] = {"merged": 0, "faded": 0, "archived": 0}

        try:
            stats["merged"] = await self.merge_overlapping_facts(user_id)
        except Exception as e:
            logger.warning(f"Fact merge failed for {user_id}: {e}")

        try:
            decay = self.db.apply_decay(user_id)
            stats["faded"] = decay["faded"]
            stats["archived"] = decay["archived"]
        except Exception as e:
            logger.warning(f"Decay failed for {user_id}: {e}")

        if stats["merged"] or stats["archived"]:
            logger.info(f"Consolidation for {user_id}: {stats}")

        return stats

    # ── Fact Merge ────────────────────────────────────────────

    async def merge_overlapping_facts(self, user_id: str) -> int:
        """Find and merge semantically overlapping facts."""
        if not self.embedder:
            return 0

        facts = self.db.get_facts(user_id, valid_only=True, limit=200)
        if len(facts) < 2:
            return 0

        clusters = self._cluster_facts(facts, threshold=0.35)

        merged_count = 0
        for cluster in clusters:
            if len(cluster) < 2:
                continue

            try:
                merged_content = await self._llm_merge(cluster)
                if not merged_content:
                    continue

                new_id = str(uuid.uuid4())[:8]
                embedding = self.embedder.embed(merged_content)
                best = max(cluster, key=lambda f: f.get("confidence", 0))

                self.db.add_fact(
                    fact_id=new_id,
                    user_id=user_id,
                    content=merged_content,
                    fact_type=best.get("fact_type", "semantic"),
                    source="merge",
                    confidence=best.get("confidence", 1.0),
                    importance=max(f.get("importance", 0.5) for f in cluster),
                    category=best.get("category"),
                    embedding=embedding,
                )

                for old in cluster:
                    self.db.invalidate_fact(old["fact_id"], superseded_by=new_id)

                merged_count += 1
                logger.debug(f"Merged {len(cluster)} facts → '{merged_content[:50]}'")
            except Exception as e:
                logger.warning(f"Merge cluster failed: {e}")

        return merged_count

    def _cluster_facts(
        self, facts: list[dict], threshold: float = 0.75
    ) -> list[list[dict]]:
        """Cluster facts by embedding similarity (greedy)."""
        if not self.embedder:
            return []

        embeddings = {}
        for f in facts:
            if f.get("content"):
                embeddings[f["fact_id"]] = self.embedder.embed(f["content"])

        used: set[str] = set()
        clusters: list[list[dict]] = []

        for i, f1 in enumerate(facts):
            if f1["fact_id"] in used or f1["fact_id"] not in embeddings:
                continue
            cluster = [f1]
            used.add(f1["fact_id"])

            for f2 in facts[i + 1:]:
                if f2["fact_id"] in used or f2["fact_id"] not in embeddings:
                    continue
                dist = self._cosine_distance(
                    embeddings[f1["fact_id"]], embeddings[f2["fact_id"]]
                )
                if dist < threshold:
                    cluster.append(f2)
                    used.add(f2["fact_id"])

            if len(cluster) >= 2:
                clusters.append(cluster)

        return clusters

    @staticmethod
    def _cosine_distance(a: list[float], b: list[float]) -> float:
        """Cosine distance (0=identical, 2=opposite)."""
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        if na == 0 or nb == 0:
            return 2.0
        return 1.0 - dot / (na * nb)

    async def _llm_merge(self, cluster: list[dict]) -> str:
        """LLM merges overlapping facts into one rich fact."""
        facts_text = "\n".join(f"- {f['content']}" for f in cluster)
        prompt = (
            "Aşağıdaki bilgiler aynı konuyla ilgili. "
            "Hepsini tek bir kapsamlı cümleye birleştir. "
            "Sadece birleştirilmiş cümleyi döndür.\n\n"
            f"Bilgiler:\n{facts_text}"
        )

        try:
            response = await llm_provider.achat(
                [
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": prompt},
                ],
                model=self.model,
                temperature=0.1,
                max_tokens=200,
            )
            return (response.content or "").strip()
        except Exception as e:
            logger.warning(f"LLM merge failed: {e}")
            return ""
