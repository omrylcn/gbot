"""FastAPI dependency injection — pull singletons from app.state."""

from __future__ import annotations

from fastapi import Request

from graphbot.agent.runner import GraphRunner
from graphbot.core.config.schema import Config
from graphbot.memory.store import MemoryStore


def get_config(request: Request) -> Config:
    """Get Config singleton from app state."""
    return request.app.state.config


def get_db(request: Request) -> MemoryStore:
    """Get MemoryStore singleton from app state."""
    return request.app.state.db


def get_runner(request: Request) -> GraphRunner:
    """Get GraphRunner singleton from app state."""
    return request.app.state.runner
