"""Pydantic data models — domain-agnostic (Recipe → Item)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ════════════════════════════════════════════════════════════
# DOMAIN MODELS
# ════════════════════════════════════════════════════════════


class Item(BaseModel):
    """Generic knowledge-base item (generalized from Recipe)."""

    id: str
    title: str
    description: str = ""
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ItemCard(BaseModel):
    """Compact item for API responses / context."""

    id: str
    title: str
    description: str | None = None
    category: str | None = None


# ════════════════════════════════════════════════════════════
# API REQUEST / RESPONSE
# ════════════════════════════════════════════════════════════


class ChatRequest(BaseModel):
    message: str
    user_id: str | None = None
    session_id: str | None = None


class ChatResponse(BaseModel):
    response: str
    session_id: str


class SessionInfo(BaseModel):
    session_id: str
    channel: str = "api"
    started_at: str
    ended_at: str | None = None
    summary: str | None = None
    token_count: int = 0
    close_reason: str | None = None


class UserContextResponse(BaseModel):
    context_text: str
    preferences: dict[str, Any] = Field(default_factory=dict)
    favorites: list[ItemCard] = Field(default_factory=list)
    recent_activities: list[dict[str, Any]] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    agent_ready: bool
    version: str = ""
    items_count: int = 0


# ════════════════════════════════════════════════════════════
# AUTH
# ════════════════════════════════════════════════════════════


class RegisterRequest(BaseModel):
    user_id: str
    password: str
    name: str


class LoginRequest(BaseModel):
    user_id: str
    password: str


class AuthResponse(BaseModel):
    success: bool
    message: str
    user_id: str | None = None
    name: str | None = None
