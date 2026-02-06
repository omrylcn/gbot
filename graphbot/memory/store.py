"""SQLite-based memory store for GraphBot.

Generalized from ascibot's MemoryStore.  10 tables:
    users, user_channels, sessions, messages,
    agent_memory, user_notes, activity_logs,
    favorites, preferences, cron_jobs
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger


class MemoryStore:
    """SQLite memory — single source of truth."""

    def __init__(self, db_path: str = "data/graphbot.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        logger.info(f"MemoryStore initialized: {db_path}")

    @contextmanager
    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._get_conn() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    # ════════════════════════════════════════════════════════════
    # USERS
    # ════════════════════════════════════════════════════════════

    def get_or_create_user(self, user_id: str, name: str | None = None) -> str:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT user_id FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            if row:
                return user_id
            conn.execute(
                "INSERT INTO users (user_id, name) VALUES (?, ?)",
                (user_id, name),
            )
            conn.commit()
            logger.info(f"New user created: {user_id}")
        return user_id

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT user_id, name, created_at FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def user_exists(self, user_id: str) -> bool:
        with self._get_conn() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM users WHERE user_id = ?", (user_id,)
                ).fetchone()
                is not None
            )

    # ════════════════════════════════════════════════════════════
    # USER CHANNELS (cross-channel identity)
    # ════════════════════════════════════════════════════════════

    def link_channel(self, user_id: str, channel: str, channel_user_id: str) -> None:
        """Link a channel identity to a user."""
        self.get_or_create_user(user_id)
        with self._get_conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO user_channels
                   (user_id, channel, channel_user_id) VALUES (?, ?, ?)""",
                (user_id, channel, channel_user_id),
            )
            conn.commit()

    def resolve_user(self, channel: str, channel_user_id: str) -> str | None:
        """Resolve channel identity → user_id."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT user_id FROM user_channels WHERE channel = ? AND channel_user_id = ?",
                (channel, channel_user_id),
            ).fetchone()
        return row["user_id"] if row else None

    # ════════════════════════════════════════════════════════════
    # SESSIONS (token-based)
    # ════════════════════════════════════════════════════════════

    def create_session(self, user_id: str, channel: str = "api") -> str:
        self.get_or_create_user(user_id)
        session_id = str(uuid.uuid4())
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO sessions (session_id, user_id, channel) VALUES (?, ?, ?)",
                (session_id, user_id, channel),
            )
            conn.commit()
        logger.info(f"Session created: {session_id} for {user_id}")
        return session_id

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_active_session(self, user_id: str) -> dict[str, Any] | None:
        """Get the user's currently open session (ended_at IS NULL)."""
        with self._get_conn() as conn:
            row = conn.execute(
                """SELECT * FROM sessions
                   WHERE user_id = ? AND ended_at IS NULL
                   ORDER BY started_at DESC LIMIT 1""",
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def end_session(
        self,
        session_id: str,
        summary: str | None = None,
        close_reason: str = "manual",
    ) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """UPDATE sessions
                   SET ended_at = CURRENT_TIMESTAMP, summary = ?, close_reason = ?
                   WHERE session_id = ?""",
                (summary, close_reason, session_id),
            )
            conn.commit()
        logger.info(f"Session ended: {session_id} ({close_reason})")

    def update_session_token_count(self, session_id: str, token_count: int) -> None:
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE sessions SET token_count = ? WHERE session_id = ?",
                (token_count, session_id),
            )
            conn.commit()

    def get_user_sessions(self, user_id: str, limit: int = 10) -> list[dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT session_id, channel, started_at, ended_at,
                          summary, token_count, close_reason
                   FROM sessions WHERE user_id = ?
                   ORDER BY started_at DESC LIMIT ?""",
                (user_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_last_session_summary(self, user_id: str) -> str | None:
        """Get previous (closed) session's summary for context."""
        with self._get_conn() as conn:
            row = conn.execute(
                """SELECT summary FROM sessions
                   WHERE user_id = ? AND ended_at IS NOT NULL AND summary IS NOT NULL
                   ORDER BY ended_at DESC LIMIT 1""",
                (user_id,),
            ).fetchone()
        return row["summary"] if row else None

    # ════════════════════════════════════════════════════════════
    # MESSAGES
    # ════════════════════════════════════════════════════════════

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_calls: str | None = None,
    ) -> int:
        with self._get_conn() as conn:
            cursor = conn.execute(
                """INSERT INTO messages (session_id, role, content, tool_calls)
                   VALUES (?, ?, ?, ?)""",
                (session_id, role, content, tool_calls),
            )
            conn.commit()
            return cursor.lastrowid or 0

    def get_session_messages(self, session_id: str) -> list[dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT role, content, tool_calls, created_at
                   FROM messages WHERE session_id = ?
                   ORDER BY created_at ASC""",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_recent_messages(self, session_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT role, content, tool_calls
                   FROM messages WHERE session_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (session_id, limit),
            ).fetchall()
        return list(reversed([dict(r) for r in rows]))

    # ════════════════════════════════════════════════════════════
    # AGENT MEMORY (nanobot MEMORY.md → SQLite)
    # ════════════════════════════════════════════════════════════

    def write_memory(self, key: str, content: str, user_id: str | None = None) -> None:
        """Write / update an agent memory entry."""
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO agent_memory (user_id, key, content)
                   VALUES (?, ?, ?)
                   ON CONFLICT(user_id, key) DO UPDATE SET
                       content = excluded.content,
                       updated_at = CURRENT_TIMESTAMP""",
                (user_id or "", key, content),
            )
            conn.commit()

    def read_memory(self, key: str, user_id: str | None = None) -> str | None:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT content FROM agent_memory WHERE user_id = ? AND key = ?",
                (user_id or "", key),
            ).fetchone()
        return row["content"] if row else None

    def get_all_memory(self, user_id: str | None = None) -> list[dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT key, content, updated_at FROM agent_memory WHERE user_id = ?",
                (user_id or "",),
            ).fetchall()
        return [dict(r) for r in rows]

    # ════════════════════════════════════════════════════════════
    # USER NOTES (learned facts)
    # ════════════════════════════════════════════════════════════

    def add_note(
        self, user_id: str, note: str, source: str = "conversation"
    ) -> int:
        self.get_or_create_user(user_id)
        with self._get_conn() as conn:
            cursor = conn.execute(
                "INSERT INTO user_notes (user_id, note, source) VALUES (?, ?, ?)",
                (user_id, note, source),
            )
            conn.commit()
            return cursor.lastrowid or 0

    def get_notes(self, user_id: str, limit: int = 50) -> list[str]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT note FROM user_notes
                   WHERE user_id = ? ORDER BY created_at DESC LIMIT ?""",
                (user_id, limit),
            ).fetchall()
        return [r["note"] for r in rows]

    # ════════════════════════════════════════════════════════════
    # ACTIVITY LOGS (ascibot meal_logs → genel)
    # ════════════════════════════════════════════════════════════

    def log_activity(
        self,
        user_id: str,
        item_title: str,
        activity_type: str = "used",
        item_id: str | None = None,
    ) -> int:
        self.get_or_create_user(user_id)
        with self._get_conn() as conn:
            cursor = conn.execute(
                """INSERT INTO activity_logs
                   (user_id, item_id, item_title, activity_type)
                   VALUES (?, ?, ?, ?)""",
                (user_id, item_id, item_title, activity_type),
            )
            conn.commit()
            return cursor.lastrowid or 0

    def get_recent_activities(
        self, user_id: str, days: int = 7
    ) -> list[dict[str, Any]]:
        cutoff = date.today() - timedelta(days=days)
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT item_id, item_title, activity_type, activity_date
                   FROM activity_logs
                   WHERE user_id = ? AND activity_date >= ?
                   ORDER BY activity_date DESC""",
                (user_id, cutoff),
            ).fetchall()
        return [dict(r) for r in rows]

    # ════════════════════════════════════════════════════════════
    # FAVORITES
    # ════════════════════════════════════════════════════════════

    def add_favorite(self, user_id: str, item_id: str, item_title: str) -> None:
        self.get_or_create_user(user_id)
        with self._get_conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO favorites
                   (user_id, item_id, item_title) VALUES (?, ?, ?)""",
                (user_id, item_id, item_title),
            )
            conn.commit()

    def remove_favorite(self, user_id: str, item_id: str) -> None:
        with self._get_conn() as conn:
            conn.execute(
                "DELETE FROM favorites WHERE user_id = ? AND item_id = ?",
                (user_id, item_id),
            )
            conn.commit()

    def get_favorites(self, user_id: str) -> list[dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT item_id, item_title, added_at FROM favorites
                   WHERE user_id = ? ORDER BY added_at DESC""",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def is_favorite(self, user_id: str, item_id: str) -> bool:
        with self._get_conn() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM favorites WHERE user_id = ? AND item_id = ?",
                    (user_id, item_id),
                ).fetchone()
                is not None
            )

    # ════════════════════════════════════════════════════════════
    # PREFERENCES (flexible JSON blob)
    # ════════════════════════════════════════════════════════════

    def get_preferences(self, user_id: str) -> dict[str, Any]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT data FROM preferences WHERE user_id = ?", (user_id,)
            ).fetchone()
        return json.loads(row["data"]) if row else {}

    def update_preferences(self, user_id: str, data: dict[str, Any]) -> None:
        """Merge new data into existing preferences."""
        self.get_or_create_user(user_id)
        current = self.get_preferences(user_id)
        current.update(data)
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO preferences (user_id, data)
                   VALUES (?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       data = excluded.data,
                       updated_at = CURRENT_TIMESTAMP""",
                (user_id, json.dumps(current, ensure_ascii=False)),
            )
            conn.commit()

    # ════════════════════════════════════════════════════════════
    # CRON JOBS
    # ════════════════════════════════════════════════════════════

    def add_cron_job(
        self,
        job_id: str,
        user_id: str,
        cron_expr: str,
        message: str,
        channel: str = "api",
        enabled: bool = True,
    ) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO cron_jobs
                   (job_id, user_id, cron_expr, message, channel, enabled)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (job_id, user_id, cron_expr, message, channel, int(enabled)),
            )
            conn.commit()

    def get_cron_jobs(self, user_id: str | None = None) -> list[dict[str, Any]]:
        with self._get_conn() as conn:
            if user_id:
                rows = conn.execute(
                    "SELECT * FROM cron_jobs WHERE user_id = ?", (user_id,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM cron_jobs").fetchall()
        return [dict(r) for r in rows]

    def remove_cron_job(self, job_id: str) -> None:
        with self._get_conn() as conn:
            conn.execute("DELETE FROM cron_jobs WHERE job_id = ?", (job_id,))
            conn.commit()

    # ════════════════════════════════════════════════════════════
    # COMBINED USER CONTEXT (for ContextBuilder)
    # ════════════════════════════════════════════════════════════

    def get_user_context(self, user_id: str) -> str:
        """Assemble full user context string for system prompt."""
        parts: list[str] = []

        # Notes
        notes = self.get_notes(user_id, limit=20)
        if notes:
            lines = "\n".join(f"- {n}" for n in notes)
            parts.append(f"USER NOTES:\n{lines}")

        # Recent activities
        activities = self.get_recent_activities(user_id, days=7)
        if activities:
            lines = "\n".join(
                f"- {a['activity_date']}: {a['item_title']} ({a['activity_type']})"
                for a in activities
            )
            parts.append(f"RECENT ACTIVITIES:\n{lines}")

        # Favorites
        favs = self.get_favorites(user_id)
        if favs:
            lines = "\n".join(f"- {f['item_title']}" for f in favs)
            parts.append(f"FAVORITES:\n{lines}")

        # Preferences
        prefs = self.get_preferences(user_id)
        if prefs:
            lines = "\n".join(f"- {k}: {v}" for k, v in prefs.items())
            parts.append(f"PREFERENCES:\n{lines}")

        return "\n\n".join(parts) if parts else ""


# ════════════════════════════════════════════════════════════
# SQL SCHEMA
# ════════════════════════════════════════════════════════════

_SCHEMA = """
-- 1. Users
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Cross-channel identity
CREATE TABLE IF NOT EXISTS user_channels (
    user_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    channel_user_id TEXT NOT NULL,
    PRIMARY KEY (channel, channel_user_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- 3. Sessions (token-based lifecycle)
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    channel TEXT DEFAULT 'api',
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP,
    summary TEXT,
    token_count INTEGER DEFAULT 0,
    close_reason TEXT,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, started_at DESC);

-- 4. Messages
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    tool_calls TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);

-- 5. Agent memory (nanobot MEMORY.md → structured)
CREATE TABLE IF NOT EXISTS agent_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT '',
    key TEXT NOT NULL,
    content TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, key)
);

-- 6. User notes (learned facts)
CREATE TABLE IF NOT EXISTS user_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    note TEXT NOT NULL,
    source TEXT DEFAULT 'conversation',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
CREATE INDEX IF NOT EXISTS idx_notes_user ON user_notes(user_id);

-- 7. Activity logs (ascibot meal_logs → general)
CREATE TABLE IF NOT EXISTS activity_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    item_id TEXT,
    item_title TEXT NOT NULL,
    activity_type TEXT DEFAULT 'used',
    activity_date DATE DEFAULT CURRENT_DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
CREATE INDEX IF NOT EXISTS idx_activity_user ON activity_logs(user_id, activity_date DESC);

-- 8. Favorites
CREATE TABLE IF NOT EXISTS favorites (
    user_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    item_title TEXT NOT NULL,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, item_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- 9. Preferences (flexible JSON)
CREATE TABLE IF NOT EXISTS preferences (
    user_id TEXT PRIMARY KEY,
    data TEXT NOT NULL DEFAULT '{}',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- 10. Cron jobs
CREATE TABLE IF NOT EXISTS cron_jobs (
    job_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    cron_expr TEXT NOT NULL,
    message TEXT NOT NULL,
    channel TEXT DEFAULT 'api',
    enabled INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
"""
