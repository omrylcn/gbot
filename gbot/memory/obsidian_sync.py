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
        # Faz 22I — build (surface → canonical) map so body text gets
        # ``[[canonical]]`` wikilinks. Obsidian graph view connects notes
        # only through wikilinks; plain-text mentions stay as isolated
        # nodes. Includes the canonical itself + every surface form on
        # ``memory_entity_pages.entity_surface_forms`` (e.g. "Kullanıcı"
        # → "owner", "Ömer" → "owner") so cross-page linking matches the
        # internal entity resolver.
        link_map: dict[str, str] = {}
        for p in pages:
            canonical = (p.get("entity_canonical") or "").strip()
            if not canonical:
                continue
            link_map.setdefault(canonical, canonical)
            raw_forms = p.get("entity_surface_forms") or "[]"
            try:
                forms = json.loads(raw_forms) if isinstance(raw_forms, str) else raw_forms
            except (ValueError, TypeError):
                forms = []
            for surf in forms or []:
                if not isinstance(surf, str) or not surf.strip():
                    continue
                # First writer wins so the most-specific canonical sticks
                # when two pages share a surface form.
                link_map.setdefault(surf.strip(), canonical)
        written = 0
        skipped = 0
        for page in pages:
            if page.get("stale") and not cfg.include_stale:
                skipped += 1
                continue
            entity = page.get("entity_canonical") or "_unknown"
            fname = _slugify(entity) + ".md"
            path = target / fname
            content = self._render(user_id, page, link_map=link_map)
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
    def _render(
        user_id: str,
        page: dict,
        link_map: dict[str, str] | None = None,
    ) -> str:
        """Markdown body with a small YAML frontmatter.

        ``link_map`` maps surface forms to canonical entity names (e.g.
        ``{"Ömer": "owner", "Kullanıcı": "owner", "Zeynep": "Zeynep"}``).
        Each surface mention in the body becomes ``[[canonical]]`` so
        Obsidian's graph view connects notes through wikilinks. The
        current page's canonical is excluded to avoid self-loops.
        """
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
        entity = page.get("entity_canonical", "")
        frontmatter = (
            "---\n"
            f"tags: [gbot-memory, {_slugify(user_id)}]\n"
            f"entity: {entity}\n"
            f"compiled_at: {compiled_at or 'unknown'}\n"
            f"synced_at: {ts}\n"
            f"source_facts: {json.dumps(source_facts)}\n"
            "---\n\n"
        )
        body = page.get("content_md") or ""
        if link_map:
            body = _inject_wikilinks(body, link_map, exclude_canonical=entity)
        if not body.endswith("\n"):
            body += "\n"
        return frontmatter + body


def _inject_wikilinks(
    text: str,
    link_map: dict[str, str],
    exclude_canonical: str = "",
) -> str:
    """Wrap whole-word surface mentions with ``[[canonical]]``.

    Iterates surfaces longest-first so 'Akasya AVM' wins over 'Akasya'.

    Skips:
      * surfaces whose canonical is the current page's entity (no
        self-loops)
      * matches already inside an existing ``[[...]]`` (idempotent)
      * matches inside ``[fact_id:...]`` tokens (lookarounds handle this)
    """
    sorted_surfaces = sorted(link_map.keys(), key=len, reverse=True)
    for surface in sorted_surfaces:
        canonical = link_map[surface]
        if not surface or not canonical:
            continue
        if canonical == exclude_canonical:
            continue
        # Unicode-friendly word boundaries (Python \b is ASCII-only).
        # Lookarounds reject:
        #   - any word char before/after (substring of a larger word)
        #   - '[' before or ']' after (already inside [[...]] or [fact_id:...])
        # Case-insensitive: "kullanıcı" / "Kullanıcı" / "KULLANıCı" all
        # collapse to ``[[owner]]``. Replacement string is the canonical
        # so the link target doesn't drift.
        pattern = re.compile(
            r"(?<![\w\[])" + re.escape(surface) + r"(?![\w\]])",
            re.UNICODE | re.IGNORECASE,
        )
        text = pattern.sub(f"[[{canonical}]]", text)
    return text
