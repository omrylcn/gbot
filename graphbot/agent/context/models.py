"""Context models — Pydantic models for context layer inspection."""

from __future__ import annotations

from pydantic import BaseModel


class LayerResult(BaseModel):
    """Result of building a single context layer."""

    name: str
    content: str
    chars: int
    tokens: int
    budget: int
    truncated: bool
    enabled: bool


class ContextOverride(BaseModel):
    """Runtime override for a single context layer."""

    content: str | None = None
    enabled: bool = True
