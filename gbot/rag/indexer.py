"""Index build utilities — thin wrapper over SemanticRetriever."""

from __future__ import annotations

from gbot.core.config.schema import RagConfig
from gbot.rag.retriever import SemanticRetriever


def build_index(config: RagConfig) -> SemanticRetriever:
    """Load data and build/load FAISS index. Returns ready retriever."""
    return SemanticRetriever(config)


def rebuild_index(config: RagConfig) -> SemanticRetriever:
    """Delete existing index and rebuild from scratch."""
    retriever = SemanticRetriever(config)
    retriever.rebuild_index()
    return retriever
