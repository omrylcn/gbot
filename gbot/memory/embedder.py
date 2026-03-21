"""MemoryEmbedder — sync embedding client for memory facts."""

from __future__ import annotations

import os

import httpx
from loguru import logger

from gbot.core.config.schema import MemoryEmbeddingConfig


class MemoryEmbedder:
    """Sync embedding client using OpenRouter API.

    Used by MemoryService (extraction) and ContextBuilder (retrieval).
    All calls are sync — ~100ms per call, acceptable for both
    background tasks and context building.
    """

    def __init__(self, config: MemoryEmbeddingConfig, api_key: str | None = None):
        self.model = config.model
        self.dimension = config.dimension
        self._api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self._url = "https://openrouter.ai/api/v1/embeddings"

    def embed(self, text: str) -> list[float]:
        """Embed a single text. Returns vector of self.dimension floats."""
        result = self.embed_batch([text])
        return result[0] if result else [0.0] * self.dimension

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts in one API call."""
        if not texts:
            return []
        if not self._api_key:
            logger.warning("No API key for embedding — returning zero vectors")
            return [[0.0] * self.dimension] * len(texts)

        try:
            r = httpx.post(
                self._url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"model": self.model, "input": texts},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            return [d["embedding"] for d in data["data"]]
        except Exception as e:
            logger.error(f"Embedding failed: {e}")
            return [[0.0] * self.dimension] * len(texts)
