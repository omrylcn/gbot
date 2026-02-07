"""FAISS-based semantic retriever — domain-agnostic, config-driven."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import faiss
from sentence_transformers import SentenceTransformer

from loguru import logger

from graphbot.core.config.schema import RagConfig


class SemanticRetriever:
    """
    FAISS-based semantic retriever.

    Adapted from ascibot RecipeRetriever — works with any dict items
    instead of domain-specific Recipe models.
    """

    def __init__(self, config: RagConfig) -> None:
        self.config = config
        self.items: list[dict[str, Any]] = []
        self.index: faiss.Index | None = None
        self.embedder: SentenceTransformer | None = None

        self._load()

    # ── Public API ──────────────────────────────────────────

    def search(
        self,
        query: str,
        exclude_ids: list[str] | None = None,
        top_k: int = 5,
    ) -> list[tuple[dict[str, Any], float]]:
        """Search items by semantic similarity. Returns [(item, score), ...]."""
        if not self.index or not self.embedder or not self.items:
            logger.warning("Search called but index not ready")
            return []

        exclude_ids = exclude_ids or []

        query_embedding = self.embedder.encode(
            [query],
            convert_to_numpy=True,
            normalize_embeddings=True,
        )

        search_k = min(top_k * 3, len(self.items))
        scores, indices = self.index.search(query_embedding.astype("float32"), search_k)

        results: list[tuple[dict[str, Any], float]] = []
        id_field = self.config.id_field
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue
            item = self.items[idx]

            if str(item.get(id_field, "")) in exclude_ids:
                continue

            results.append((item, float(score)))
            if len(results) >= top_k:
                break

        return results

    def get_by_id(self, item_id: str) -> dict[str, Any] | None:
        """Get item by ID field."""
        id_field = self.config.id_field
        for item in self.items:
            if str(item.get(id_field, "")) == str(item_id):
                return item
        return None

    def format_results(self, results: list[tuple[dict[str, Any], float]]) -> str:
        """Format results as LLM-readable text."""
        if not results:
            return "No items found."

        id_field = self.config.id_field
        lines: list[str] = []
        for i, (item, score) in enumerate(results, 1):
            item_id = item.get(id_field, "?")
            text = self._item_to_text(item)
            short = text[:120].replace("\n", " ")
            lines.append(f"{i}. ID: {item_id} — {short} (score: {score:.2f})")

        return "\n".join(lines)

    def rebuild_index(self) -> None:
        """Delete existing index and rebuild."""
        index_file = Path(self.config.index_path) / "index.faiss"
        if index_file.exists():
            index_file.unlink()
            logger.info("Old index deleted")
        self._build_index()

    @property
    def ready(self) -> bool:
        """Whether items + index + embedder are all loaded."""
        return bool(self.items and self.index is not None and self.embedder is not None)

    @property
    def count(self) -> int:
        """Number of loaded items."""
        return len(self.items)

    # ── Internal ────────────────────────────────────────────

    def _load(self) -> None:
        """Load embedder, items from JSON, and FAISS index."""
        logger.info("Loading embedding model: %s", self.config.embedding_model)
        self.embedder = SentenceTransformer(self.config.embedding_model)

        data_path = Path(self.config.data_source)
        if not data_path.exists():
            logger.warning("Data file not found: %s", data_path)
            self.items = []
            return

        with open(data_path, encoding="utf-8") as f:
            data = json.load(f)
        self.items = list(data) if isinstance(data, list) else []
        logger.info("Loaded %d items", len(self.items))

        if not self.items:
            return

        # Load or build index
        index_file = Path(self.config.index_path) / "index.faiss"
        if index_file.exists():
            logger.info("Loading FAISS index from %s", index_file)
            self.index = faiss.read_index(str(index_file))
        else:
            logger.info("Building new FAISS index...")
            self._build_index()

    def _build_index(self) -> None:
        """Build FAISS IndexFlatIP from items."""
        if not self.items or not self.embedder:
            return

        texts = [self._item_to_text(item) for item in self.items]

        logger.info("Generating embeddings for %d items...", len(texts))
        embeddings = self.embedder.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )

        dimension = embeddings.shape[1]
        self.index = faiss.IndexFlatIP(dimension)
        self.index.add(embeddings.astype("float32"))

        # Save index
        index_dir = Path(self.config.index_path)
        index_dir.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.index, str(index_dir / "index.faiss"))
        logger.info("Index saved to %s", index_dir)

    def _item_to_text(self, item: dict[str, Any]) -> str:
        """Convert item dict to searchable text using config template."""
        safe = defaultdict(str, {k: str(v) for k, v in item.items()})
        return self.config.text_template.format_map(safe)
