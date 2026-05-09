"""LOCOMO-mini benchmark runner — load fixture, seed memory, run queries.

Workflow:
1. Build a fresh ``MemoryStore`` (in-memory or temp file).
2. Seed ``memory_facts`` with ground-truth from ``fixtures/facts.json``.
   We bypass the LLM extraction path — we know exactly which fact_ids
   we want, so we ``add_fact`` directly. Embeddings are real (live
   OpenRouter call) or zero-vector if no key (degraded mode, recall
   numbers irrelevant).
3. Seed ``memory_relations`` likewise.
4. For each query in ``queries.json``: embed → search → record retrieved
   fact_ids + distances + latency.
5. Aggregate metrics and return.

The runner is **stateless** w.r.t. the broader app — no Runner, no
agent, no FastAPI. Just store + embedder + retrieval. Fast and
reproducible.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from gbot.core.config.schema import MemoryEmbeddingConfig
from gbot.memory.embedder import MemoryEmbedder
from gbot.memory.store import MemoryStore

from tests.memory_benchmark.metrics import QueryResult, aggregate

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@dataclass
class BenchmarkConfig:
    """Knobs for one benchmark run. Tweak in tests to A/B compare."""

    db_path: str  # tmp_path string
    user_id: str = "owner"
    top_k: int = 10
    max_distance: float | None = 0.45
    use_real_embeddings: bool = True
    embedding_model: str = "google/gemini-embedding-001"
    embedding_dim: int = 3072


def load_fixtures() -> tuple[dict, dict]:
    """Read facts.json and queries.json. Returns (facts_dict, queries_dict)."""
    with open(FIXTURES_DIR / "facts.json") as f:
        facts = json.load(f)
    with open(FIXTURES_DIR / "queries.json") as f:
        queries = json.load(f)
    return facts, queries


def seed_memory(store: MemoryStore, facts_data: dict, embedder: MemoryEmbedder) -> None:
    """Insert all ground-truth facts + relations into a fresh store.

    Embeddings are computed live. Without a real API key the embedder
    returns zero vectors — recall numbers are then meaningless but the
    rest of the pipeline still exercises the code path.
    """
    user_id = facts_data["user_id"]
    store.get_or_create_user(user_id, "Benchmark Owner")

    for fact in facts_data["facts"]:
        embedding = embedder.embed(fact["content"])
        store.add_fact(
            fact_id=fact["fact_id"],
            user_id=user_id,
            content=fact["content"],
            fact_type=fact["fact_type"],
            category=fact["category"],
            embedding=embedding,
        )

    # Relations — canonical_* fields skip resolver (benchmark stays
    # deterministic and bypasses any owner-alias mapping).
    import uuid

    for rel in facts_data.get("relations", []):
        store.add_relation(
            relation_id=str(uuid.uuid4())[:8],
            user_id=user_id,
            source_entity=rel["source"],
            relation=rel["relation"],
            target_entity=rel["target"],
            canonical_source=rel["source"],
            canonical_target=rel["target"],
        )


def run_queries(
    store: MemoryStore,
    queries_data: dict,
    embedder: MemoryEmbedder,
    cfg: BenchmarkConfig,
) -> list[QueryResult]:
    """For each query, embed → search → record."""
    results: list[QueryResult] = []
    for q in queries_data["queries"]:
        text = q["query"]
        expected = q.get("expected_fact_ids", [])

        t0 = time.perf_counter()
        emb = embedder.embed(text)
        rows = store.search_similar_facts(
            cfg.user_id, emb,
            top_k=cfg.top_k,
            max_distance=cfg.max_distance,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000

        retrieved = [r["fact_id"] for r in rows]
        distances = [round(r.get("distance") or 0.0, 4) for r in rows]
        token_estimate = sum(len(r.get("content") or "") // 4 for r in rows)

        results.append(
            QueryResult(
                qid=q["qid"],
                qtype=q["type"],
                query=text,
                expected=expected,
                retrieved=retrieved,
                distances=distances,
                latency_ms=round(elapsed_ms, 1),
                tokens=token_estimate,
            )
        )

    return results


def run_benchmark(cfg: BenchmarkConfig) -> dict:
    """Orchestrate one full benchmark cycle. Returns aggregate stats."""
    facts_data, queries_data = load_fixtures()

    store = MemoryStore(cfg.db_path)
    api_key = os.environ.get("OPENROUTER_API_KEY", "") if cfg.use_real_embeddings else ""
    embedder = MemoryEmbedder(
        MemoryEmbeddingConfig(
            provider="openrouter",
            model=cfg.embedding_model,
            dimension=cfg.embedding_dim,
        ),
        api_key=api_key,
    )

    seed_memory(store, facts_data, embedder)
    results = run_queries(store, queries_data, embedder, cfg)
    return {
        "results": [r.__dict__ for r in results],
        "stats": aggregate(results),
        "config": cfg.__dict__,
    }
