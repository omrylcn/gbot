"""Entity normalization for memory_relations.

Faz 22D — Backlinks revival. Maps surface forms ("Ömer", "Kullanıcı",
"User", "owner", "ben") to a canonical name per user, so the relations
graph collapses onto stable nodes instead of fragmenting across language /
casing variants.

Three-tier resolution:

1. **Owner aliases** — deterministic, no LLM. A small fixed set of
   self-reference words (configurable via ``OWNER_SELF_TERMS``) mapped to
   the owner's canonical username/name read from config.
2. **Per-user alias table** — ``memory_entity_aliases`` rows. Auto-populated
   when LLM later confirms an alias; can also be edited manually via
   admin/dashboard (Faz 22E).
3. **Identity fallback** — unknown surface forms map to themselves. The
   raw form is preserved in ``memory_relations.source_entity`` for audit;
   only ``canonical_*`` columns carry the resolved name.

Idempotent. Case-insensitive on lookup. Trims whitespace and trailing
punctuation. Never destroys the original surface form.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from gbot.memory.store import MemoryStore


# Self-reference words that map to the owner. Tier-1 alias set; merged with
# the configured owner.username and the user record's display name.
_OWNER_SELF_TERMS: frozenset[str] = frozenset(
    {
        "user", "kullanıcı", "kullanici", "kullanıcının",
        "ben", "kendim", "me", "myself", "owner", "the user",
    }
)

# Punctuation trimmed during normalisation. Keep apostrophes inside names
# (e.g. "O'Brien") by handling them only at edges.
_TRIM_CHARS = ".,;:!?\"'`()[]{}"


def _normalize_key(s: str) -> str:
    """Lowercase + strip + trim trailing punctuation. Used for lookup only."""
    return s.strip().strip(_TRIM_CHARS).lower()


class EntityResolver:
    """Resolve entity surface forms to canonical names per user.

    Parameters
    ----------
    db : MemoryStore
        Used to read/write the ``memory_entity_aliases`` tier-2 table.
    owner_username : str | None
        Tier-1 anchor — self-reference words map to this canonical name.
        Read from ``config.assistant.owner.username`` at construction.
    owner_display_name : str | None
        Optional secondary anchor (e.g. "Ömer" when username is "owner").
        Both are accepted as canonical when normalising; lookups against
        either return the same canonical (the username).
    """

    def __init__(
        self,
        db: "MemoryStore",
        owner_username: str | None = None,
        owner_display_name: str | None = None,
    ):
        self.db = db
        self.owner_canonical = owner_username or ""
        # Build a fast in-memory lookup for tier-1 owner aliases.
        anchors: set[str] = set(_OWNER_SELF_TERMS)
        if owner_username:
            anchors.add(_normalize_key(owner_username))
        if owner_display_name:
            anchors.add(_normalize_key(owner_display_name))
        self._owner_anchors = anchors

    # ── Public API ────────────────────────────────────────────────────

    def canonicalize(self, user_id: str, surface: str) -> str:
        """Resolve a surface form to its canonical name.

        Returns the surface form unchanged when no alias is found
        (identity fallback). Always returns a stripped string.
        """
        if not surface:
            return ""
        clean = surface.strip()
        if not clean:
            return ""
        key = _normalize_key(clean)

        # Tier 1: owner self-reference
        if self.owner_canonical and key in self._owner_anchors:
            return self.owner_canonical

        # Tier 2: per-user alias table
        alias = self.db.get_alias(user_id, clean)
        if alias:
            return alias
        # Also try lowercased form so casing variants resolve identically
        if clean != key:
            alias = self.db.get_alias(user_id, key)
            if alias:
                return alias

        # Tier 3: identity
        return clean

    def expand(self, user_id: str, canonical: str) -> set[str]:
        """All surface forms that resolve to this canonical, plus the
        canonical itself. Used by ContextBuilder to find entity mentions
        in fact text.
        """
        forms = set(self.db.get_aliases_for_canonical(user_id, canonical))
        forms.add(canonical)
        # Owner canonical pulls in self-reference terms too
        if canonical == self.owner_canonical:
            forms |= set(_OWNER_SELF_TERMS)
        return forms

    def register_alias(
        self,
        user_id: str,
        surface: str,
        canonical: str,
        source: str = "manual",
    ) -> None:
        """Persist a surface→canonical mapping. Idempotent."""
        if not surface or not canonical:
            return
        self.db.set_alias(user_id, surface.strip(), canonical.strip(), source=source)

    def merge_canonicals(
        self, user_id: str, from_canonical: str, to_canonical: str
    ) -> int:
        """Merge two canonical names — rewrite all relations + alias rows
        from one canonical to another. Used when the LLM-resolved tier
        confirms two entities are the same.

        Returns the number of relation rows updated.
        """
        if from_canonical == to_canonical:
            return 0
        with self.db._get_conn() as conn:
            # Repoint relations
            cursor = conn.execute(
                """UPDATE memory_relations
                       SET canonical_source = ?
                       WHERE user_id = ? AND canonical_source = ?""",
                (to_canonical, user_id, from_canonical),
            )
            updated = cursor.rowcount
            cursor2 = conn.execute(
                """UPDATE memory_relations
                       SET canonical_target = ?
                       WHERE user_id = ? AND canonical_target = ?""",
                (to_canonical, user_id, from_canonical),
            )
            updated += cursor2.rowcount
            # Repoint aliases
            conn.execute(
                """UPDATE memory_entity_aliases
                       SET canonical_form = ?
                       WHERE user_id = ? AND canonical_form = ?""",
                (to_canonical, user_id, from_canonical),
            )
            conn.commit()
        logger.info(
            f"merge_canonicals: {from_canonical} → {to_canonical} for {user_id} "
            f"({updated} relation rows updated)"
        )
        return updated

    # ── Backfill helper (one-shot, used by migration tooling) ────────

    def backfill_relations(self, user_id: str | None = None) -> int:
        """Populate canonical_source/canonical_target for relations missing
        them. Safe to re-run; only touches NULL rows.

        Returns the number of rows updated.
        """
        with self.db._get_conn() as conn:
            params: list = []
            sql = """SELECT relation_id, user_id, source_entity, target_entity
                     FROM memory_relations
                     WHERE canonical_source IS NULL OR canonical_target IS NULL"""
            if user_id:
                sql += " AND user_id = ?"
                params.append(user_id)
            rows = conn.execute(sql, params).fetchall()
        updated = 0
        for r in rows:
            cs = self.canonicalize(r["user_id"], r["source_entity"])
            ct = self.canonicalize(r["user_id"], r["target_entity"])
            with self.db._get_conn() as conn:
                conn.execute(
                    """UPDATE memory_relations
                           SET canonical_source = ?, canonical_target = ?
                           WHERE relation_id = ?""",
                    (cs, ct, r["relation_id"]),
                )
                conn.commit()
            updated += 1
        if updated:
            logger.info(f"backfill_relations: {updated} rows canonicalized")
        return updated
