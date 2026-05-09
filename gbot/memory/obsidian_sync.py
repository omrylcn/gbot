"""Faz 22G Aşama 4 — entity pages → Obsidian vault sync.

Each sync run dumps every fresh ``memory_entity_pages.content_md`` row
into a markdown file under ``vault_path/gbot/<user_id>/<entity>.md``,
with a YAML frontmatter block carrying provenance (compiled_at,
source_fact_ids). Stale pages are skipped by default so Obsidian
doesn't see in-flight output.

Trigger paths:

* Cron processor ``memory_obsidian_sync`` (registered per-user at
  startup when ``memory.obsidian_sync.enabled``)
* Admin endpoint ``POST /admin/memory/{user}/obsidian-sync/run``
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger


_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _slugify(name: str) -> str:
    """File-safe slug for an entity / user id. Empty input → 'unknown'."""
    cleaned = _SLUG_RE.sub("-", (name or "").strip())
    cleaned = cleaned.strip("-._") or "unknown"
    return cleaned[:120]


class ObsidianSyncer:
    """Writes entity pages to a local Obsidian vault.

    The class doesn't own scheduling — it's invoked by the cron
    scheduler or the admin endpoint. Idempotent: re-running overwrites
    existing files when content changes, otherwise no-ops.
    """

    def __init__(self, db: Any, config: Any) -> None:
        self.db = db
        self.config = config

    def vault_dir(self, user_id: str) -> Path:
        """Where this user's pages live: ``<vault>/gbot/<user_id>/``."""
        cfg = self.config.obsidian_sync if self.config else None
        raw = (cfg.vault_path if cfg else "") or "~/Obsidian/Memories"
        return (Path(raw).expanduser() / "gbot" / _slugify(user_id)).resolve()

    def run(self, user_id: str) -> dict[str, Any]:
        """Sync all eligible entity pages for ``user_id`` to disk."""
        if not self.config or not self.config.obsidian_sync.enabled:
            logger.debug(f"obsidian_sync disabled, skipping for {user_id}")
            return {"written": 0, "skipped": 0, "deleted": 0, "enabled": False}

        cfg = self.config.obsidian_sync
        target = self.vault_dir(user_id)
        target.mkdir(parents=True, exist_ok=True)

        pages = self.db.list_entity_pages(user_id) or []
        written = 0
        skipped = 0
        for page in pages:
            if page.get("stale") and not cfg.include_stale:
                skipped += 1
                continue
            entity = page.get("entity_canonical") or "_unknown"
            fname = _slugify(entity) + ".md"
            path = target / fname
            content = self._render(user_id, page)
            if path.exists() and path.read_text(encoding="utf-8") == content:
                skipped += 1
                continue
            path.write_text(content, encoding="utf-8")
            written += 1

        logger.info(
            f"obsidian_sync({user_id}): {written} written, "
            f"{skipped} skipped, vault={target}"
        )
        return {
            "written": written,
            "skipped": skipped,
            "deleted": 0,
            "enabled": True,
            "vault_dir": str(target),
        }

    @staticmethod
    def _render(user_id: str, page: dict) -> str:
        """Markdown body with a small YAML frontmatter."""
        compiled_at = page.get("last_compiled_at") or page.get("created_at")
        source_facts = page.get("source_fact_ids")
        if isinstance(source_facts, str):
            try:
                source_facts = json.loads(source_facts)
            except (TypeError, ValueError):
                source_facts = []
        if not isinstance(source_facts, list):
            source_facts = []

        ts = datetime.utcnow().isoformat()
        frontmatter = (
            "---\n"
            f"tags: [gbot-memory, {_slugify(user_id)}]\n"
            f"entity: {page.get('entity_canonical', '')}\n"
            f"compiled_at: {compiled_at or 'unknown'}\n"
            f"synced_at: {ts}\n"
            f"source_facts: {json.dumps(source_facts)}\n"
            "---\n\n"
        )
        body = page.get("content_md") or ""
        if not body.endswith("\n"):
            body += "\n"
        return frontmatter + body
