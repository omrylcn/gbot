"""Shared bootstrap helpers for memory-pipeline suites.

The memory suites all need a ``MemoryService`` instance configured to
talk to a specific model. They do *not* need a working DB — extraction,
AUDN, and page-compile prompts never read from the database. We
construct the service via ``__new__`` and patch only the fields we
need, mirroring the original pytest pattern.
"""

from __future__ import annotations

import json
from pathlib import Path

from gbot.agent.profiles import get_agent_md
from gbot.core.config.loader import load_config
from gbot.memory.extraction import MemoryService

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def load_fixture(name: str) -> list[dict]:
    """Read ``fixtures/<name>.json`` and return its ``cases`` list."""
    path = FIXTURES_DIR / f"{name}.json"
    return json.loads(path.read_text())["cases"]


def make_memory_service(model: str) -> MemoryService:
    """Construct a MemoryService wired to ``model``, no DB required."""
    cfg = load_config()
    service = MemoryService.__new__(MemoryService)
    service.db = None
    service.model = model
    service.config = cfg.memory
    service.embedder = None
    service.resolver = None
    service.entity_compiler = None
    service._system_prompt = get_agent_md("memory") or ""
    service._update_model = model
    return service
