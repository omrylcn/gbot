"""SQLite-based memory store for GraphBot.

Generalized from ascibot's MemoryStore.  11 tables:
    users, user_channels, sessions, messages,
    agent_memory, user_notes,
    favorites, preferences, cron_jobs, api_keys
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
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
            self._migrate(conn)
            conn.commit()

    def _migrate(self, conn) -> None:
        """Add columns/tables missing in existing databases."""
        # Users table migrations
        cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "password_hash" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
        if "role" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'")
        # Migrate legacy role='user' → 'member'
        conn.execute("UPDATE users SET role = 'member' WHERE role = 'user'")

        # Cron jobs: LightAgent columns (Faz 13)
        cron_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(cron_jobs)").fetchall()
        }
        for col, ddl in [
            ("agent_prompt", "TEXT"),
            ("agent_tools", "TEXT"),
            ("agent_model", "TEXT"),
            ("notify_condition", "TEXT DEFAULT 'always'"),
            ("consecutive_failures", "INTEGER DEFAULT 0"),
            ("last_error", "TEXT"),
        ]:
            if col not in cron_cols:
                conn.execute(f"ALTER TABLE cron_jobs ADD COLUMN {col} {ddl}")

        # Reminders: recurring + agent support
        rem_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(reminders)").fetchall()
        }
        for col, ddl in [
            ("cron_expr", "TEXT"),
            ("agent_prompt", "TEXT"),
            ("agent_tools", "TEXT"),
            ("processor", "TEXT DEFAULT 'static'"),
            ("plan_json", "TEXT"),
        ]:
            if col not in rem_cols:
                conn.execute(f"ALTER TABLE reminders ADD COLUMN {col} {ddl}")

        # Cron jobs: processor + plan_json (delegation refactor)
        for col, ddl in [
            ("processor", "TEXT DEFAULT 'agent'"),
            ("plan_json", "TEXT"),
        ]:
            if col not in cron_cols:
                conn.execute(f"ALTER TABLE cron_jobs ADD COLUMN {col} {ddl}")

        # Delegation log table (may not exist in older DBs)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS delegation_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                task_description TEXT NOT NULL,
                execution_type TEXT NOT NULL,
                processor_type TEXT NOT NULL,
                reference_id TEXT,
                plan_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_delegation_user
                ON delegation_log(user_id, created_at DESC);
        """)

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
                "SELECT user_id, name, password_hash, role, created_at FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def set_user_role(self, user_id: str, role: str) -> None:
        """Update user role (owner, member, guest)."""
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE users SET role = ? WHERE user_id = ?",
                (role, user_id),
            )
            conn.commit()
        logger.info(f"User {user_id} role set to: {role}")

    def user_exists(self, user_id: str) -> bool:
        with self._get_conn() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM users WHERE user_id = ?", (user_id,)
                ).fetchone()
                is not None
            )

    def list_users(self) -> list[dict[str, Any]]:
        """List all users with their linked channels."""
        with self._get_conn() as conn:
            users = conn.execute(
                "SELECT user_id, name, created_at FROM users ORDER BY created_at"
            ).fetchall()
        result = []
        for u in users:
            user = dict(u)
            user["channels"] = self.get_user_channels(u["user_id"])
            result.append(user)
        return result

    def get_user_channels(self, user_id: str) -> list[dict[str, Any]]:
        """Get all channel links for a user."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT channel, channel_user_id, metadata FROM user_channels WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_user(self, user_id: str) -> bool:
        """Delete user and all channel links. Returns True if user existed."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM user_channels WHERE user_id = ?", (user_id,))
            cursor = conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
            conn.commit()
        return cursor.rowcount > 0

    # ════════════════════════════════════════════════════════════
    # AUTH (password + API keys)
    # ════════════════════════════════════════════════════════════

    def set_password(self, user_id: str, password_hash: str) -> None:
        """Set password hash for a user."""
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE user_id = ?",
                (password_hash, user_id),
            )
            conn.commit()

    def get_password_hash(self, user_id: str) -> str | None:
        """Get password hash for a user."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT password_hash FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        return row["password_hash"] if row else None

    def create_api_key(
        self,
        key_id: str,
        user_id: str,
        key_hash: str,
        name: str | None = None,
        expires_at: str | None = None,
    ) -> None:
        """Store a hashed API key."""
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO api_keys (key_id, user_id, key_hash, name, expires_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (key_id, user_id, key_hash, name, expires_at),
            )
            conn.commit()

    def get_api_key(self, key_id: str) -> dict[str, Any] | None:
        """Get API key by key_id."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM api_keys WHERE key_id = ?", (key_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_api_keys(self, user_id: str) -> list[dict[str, Any]]:
        """List all API keys for a user."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT key_id, name, created_at, expires_at, is_active "
                "FROM api_keys WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def deactivate_api_key(self, key_id: str) -> bool:
        """Deactivate an API key. Returns True if key existed."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE api_keys SET is_active = FALSE WHERE key_id = ?", (key_id,)
            )
            conn.commit()
        return cursor.rowcount > 0

    def find_api_key_by_hash(self, key_hash: str) -> dict[str, Any] | None:
        """Find active, non-expired API key by its hash."""
        with self._get_conn() as conn:
            row = conn.execute(
                """SELECT key_id, user_id, name, expires_at FROM api_keys
                   WHERE key_hash = ? AND is_active = TRUE
                   AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)""",
                (key_hash,),
            ).fetchone()
        return dict(row) if row else None

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

    def update_channel_metadata(
        self, channel: str, channel_user_id: str, metadata: dict[str, Any]
    ) -> None:
        """Merge metadata into a channel identity (e.g. chat_id for Telegram)."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT metadata FROM user_channels WHERE channel = ? AND channel_user_id = ?",
                (channel, channel_user_id),
            ).fetchone()
            if not row:
                return
            current = json.loads(row["metadata"] or "{}")
            current.update(metadata)
            conn.execute(
                "UPDATE user_channels SET metadata = ? WHERE channel = ? AND channel_user_id = ?",
                (json.dumps(current, ensure_ascii=False), channel, channel_user_id),
            )
            conn.commit()

    def get_channel_metadata(self, user_id: str, channel: str) -> dict[str, Any]:
        """Get metadata for a user's channel identity."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT metadata FROM user_channels WHERE user_id = ? AND channel = ?",
                (user_id, channel),
            ).fetchone()
        return json.loads(row["metadata"] or "{}") if row else {}

    def get_channel_link(self, user_id: str, channel: str) -> dict[str, Any] | None:
        """Get channel link: {channel_user_id, metadata}."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT channel_user_id, metadata FROM user_channels WHERE user_id = ? AND channel = ?",
                (user_id, channel),
            ).fetchone()
        if not row:
            return None
        return {
            "channel_user_id": row["channel_user_id"],
            "metadata": json.loads(row["metadata"] or "{}"),
        }

    def update_channel_metadata_by_user(
        self, user_id: str, channel: str, metadata: dict[str, Any]
    ) -> None:
        """Merge metadata by user_id + channel (token-based model)."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT metadata FROM user_channels WHERE user_id = ? AND channel = ?",
                (user_id, channel),
            ).fetchone()
            if not row:
                return
            current = json.loads(row["metadata"] or "{}")
            current.update(metadata)
            conn.execute(
                "UPDATE user_channels SET metadata = ? WHERE user_id = ? AND channel = ?",
                (json.dumps(current, ensure_ascii=False), user_id, channel),
            )
            conn.commit()

    # ════════════════════════════════════════════════════════════
    # SESSIONS (token-based)
    # ════════════════════════════════════════════════════════════

    def create_session(
        self, user_id: str, channel: str = "api", session_id: str | None = None,
    ) -> str:
        self.get_or_create_user(user_id)
        if session_id is None:
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

    def get_active_session(
        self, user_id: str, channel: str | None = None
    ) -> dict[str, Any] | None:
        """Get the user's currently open session (ended_at IS NULL).

        If channel is provided, returns only sessions for that channel.
        This ensures telegram sessions stay separate from api sessions.
        """
        with self._get_conn() as conn:
            if channel:
                row = conn.execute(
                    """SELECT * FROM sessions
                       WHERE user_id = ? AND channel = ? AND ended_at IS NULL
                       ORDER BY started_at DESC LIMIT 1""",
                    (user_id, channel),
                ).fetchone()
            else:
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
        tool_call_id: str | None = None,
    ) -> int:
        with self._get_conn() as conn:
            cursor = conn.execute(
                """INSERT INTO messages (session_id, role, content, tool_calls, tool_call_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, role, content, tool_calls, tool_call_id),
            )
            conn.commit()
            return cursor.lastrowid or 0

    def get_session_messages(self, session_id: str) -> list[dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT role, content, tool_calls, tool_call_id, created_at
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

    def remove_preference(self, user_id: str, key: str) -> bool:
        """Remove a single key from user preferences. Returns True if removed."""
        current = self.get_preferences(user_id)
        if key not in current:
            return False
        del current[key]
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
        return True

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
        agent_prompt: str | None = None,
        agent_tools: str | None = None,
        agent_model: str | None = None,
        notify_condition: str = "always",
        processor: str = "agent",
        plan_json: str | None = None,
    ) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO cron_jobs
                   (job_id, user_id, cron_expr, message, channel, enabled,
                    agent_prompt, agent_tools, agent_model, notify_condition,
                    processor, plan_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job_id, user_id, cron_expr, message, channel, int(enabled),
                    agent_prompt, agent_tools, agent_model, notify_condition,
                    processor, plan_json,
                ),
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

    # ── Reminders (standalone table, no LLM) ─────────────────

    def add_reminder(
        self,
        reminder_id: str,
        user_id: str,
        run_at: str,
        message: str,
        channel: str = "telegram",
        cron_expr: str | None = None,
        agent_prompt: str | None = None,
        agent_tools: str | None = None,
        processor: str = "static",
        plan_json: str | None = None,
    ) -> None:
        """Add a reminder to the reminders table.

        Parameters
        ----------
        cron_expr : str, optional
            Cron expression for recurring reminders.
        agent_prompt : str, optional
            If set, LightAgent runs with this prompt instead of sending static text.
        agent_tools : str, optional
            JSON list of tool names for the LightAgent.
        processor : str
            Processor type: "static", "function", or "agent".
        plan_json : str, optional
            JSON with processor-specific config (tool_name/tool_args or prompt/tools).
        """
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO reminders
                   (reminder_id, user_id, run_at, message, channel, cron_expr,
                    agent_prompt, agent_tools, processor, plan_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (reminder_id, user_id, run_at, message, channel, cron_expr,
                 agent_prompt, agent_tools, processor, plan_json),
            )
            conn.commit()

    def get_pending_reminders(
        self, user_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Get pending reminders."""
        with self._get_conn() as conn:
            if user_id:
                rows = conn.execute(
                    "SELECT * FROM reminders WHERE status = 'pending' AND user_id = ?",
                    (user_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM reminders WHERE status = 'pending'"
                ).fetchall()
        return [dict(r) for r in rows]

    def mark_reminder_sent(self, reminder_id: str) -> None:
        """Mark a reminder as successfully sent."""
        with self._get_conn() as conn:
            conn.execute(
                """UPDATE reminders
                   SET status = 'sent', sent_at = CURRENT_TIMESTAMP
                   WHERE reminder_id = ?""",
                (reminder_id,),
            )
            conn.commit()

    def mark_reminder_failed(self, reminder_id: str, error: str) -> None:
        """Mark a reminder as failed and increment retry count."""
        with self._get_conn() as conn:
            conn.execute(
                """UPDATE reminders
                   SET retry_count = retry_count + 1, last_error = ?,
                       status = CASE WHEN retry_count >= 2 THEN 'failed' ELSE 'pending' END
                   WHERE reminder_id = ?""",
                (error, reminder_id),
            )
            conn.commit()

    def cancel_reminder(self, reminder_id: str) -> bool:
        """Cancel a pending reminder. Returns True if cancelled."""
        with self._get_conn() as conn:
            cur = conn.execute(
                """UPDATE reminders SET status = 'cancelled'
                   WHERE reminder_id = ? AND status = 'pending'""",
                (reminder_id,),
            )
            conn.commit()
        return cur.rowcount > 0

    def remove_reminder(self, reminder_id: str) -> None:
        """Delete a reminder from the table."""
        with self._get_conn() as conn:
            conn.execute(
                "DELETE FROM reminders WHERE reminder_id = ?", (reminder_id,),
            )
            conn.commit()

    # ── Cron execution log ─────────────────────────────────────

    def log_cron_execution(
        self,
        job_id: str,
        result: str,
        status: str = "success",
        tokens_used: int = 0,
        duration_ms: int = 0,
    ) -> None:
        """Record a cron job execution result."""
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO cron_execution_log
                   (job_id, result, status, tokens_used, duration_ms)
                   VALUES (?, ?, ?, ?, ?)""",
                (job_id, result, status, tokens_used, duration_ms),
            )
            conn.commit()

    def get_cron_execution_log(
        self, job_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Get recent execution log entries for a cron job."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM cron_execution_log
                   WHERE job_id = ? ORDER BY executed_at DESC LIMIT ?""",
                (job_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def increment_cron_failures(self, job_id: str, error: str) -> int:
        """Increment consecutive failure count and record error. Returns new count."""
        with self._get_conn() as conn:
            conn.execute(
                """UPDATE cron_jobs
                   SET consecutive_failures = consecutive_failures + 1,
                       last_error = ?
                   WHERE job_id = ?""",
                (error, job_id),
            )
            conn.commit()
            row = conn.execute(
                "SELECT consecutive_failures FROM cron_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return row["consecutive_failures"] if row else 0

    def reset_cron_failures(self, job_id: str) -> None:
        """Reset consecutive failure count after successful execution."""
        with self._get_conn() as conn:
            conn.execute(
                """UPDATE cron_jobs
                   SET consecutive_failures = 0, last_error = NULL
                   WHERE job_id = ?""",
                (job_id,),
            )
            conn.commit()

    # ── System events (background → agent) ───────────────────

    def add_system_event(
        self,
        user_id: str,
        source: str,
        event_type: str,
        payload: str,
    ) -> int:
        """Create a system event. Returns the event ID."""
        with self._get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO system_events
                   (user_id, source, event_type, payload)
                   VALUES (?, ?, ?, ?)""",
                (user_id, source, event_type, payload),
            )
            conn.commit()
            return cur.lastrowid

    def get_undelivered_events(
        self, user_id: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Get undelivered system events for a user."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM system_events
                   WHERE user_id = ? AND is_delivered = FALSE
                   ORDER BY created_at ASC LIMIT ?""",
                (user_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_events_delivered(self, event_ids: list[int]) -> None:
        """Mark system events as delivered."""
        if not event_ids:
            return
        placeholders = ",".join("?" for _ in event_ids)
        with self._get_conn() as conn:
            conn.execute(
                f"UPDATE system_events SET is_delivered = TRUE WHERE id IN ({placeholders})",
                event_ids,
            )
            conn.commit()

    # ── Background tasks ─────────────────────────────────────

    def create_background_task(
        self, task_id: str, user_id: str, description: str,
        parent_session: str | None = None, fallback_channel: str | None = None,
    ) -> None:
        """Record a new background task as running."""
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO background_tasks
                   (task_id, user_id, task_description, parent_session, fallback_channel)
                   VALUES (?, ?, ?, ?, ?)""",
                (task_id, user_id, description, parent_session, fallback_channel),
            )
            conn.commit()

    def complete_background_task(self, task_id: str, result: str) -> None:
        """Mark a background task as completed with result."""
        with self._get_conn() as conn:
            conn.execute(
                """UPDATE background_tasks
                   SET status = 'completed', result = ?,
                       completed_at = CURRENT_TIMESTAMP
                   WHERE task_id = ?""",
                (result, task_id),
            )
            conn.commit()

    def fail_background_task(self, task_id: str, error: str) -> None:
        """Mark a background task as failed."""
        with self._get_conn() as conn:
            conn.execute(
                """UPDATE background_tasks
                   SET status = 'failed', error = ?,
                       completed_at = CURRENT_TIMESTAMP
                   WHERE task_id = ?""",
                (error, task_id),
            )
            conn.commit()

    def get_background_task(self, task_id: str) -> dict[str, Any] | None:
        """Get a background task by ID."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM background_tasks WHERE task_id = ?", (task_id,),
            ).fetchone()
        return dict(row) if row else None

    # ════════════════════════════════════════════════════════════
    # DELEGATION LOG (planner decision audit trail)
    # ════════════════════════════════════════════════════════════

    def log_delegation(
        self,
        user_id: str,
        task_description: str,
        execution_type: str,
        processor_type: str,
        reference_id: str | None = None,
        plan_json: str | None = None,
    ) -> int:
        """Record a delegation planner decision."""
        with self._get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO delegation_log
                   (user_id, task_description, execution_type, processor_type,
                    reference_id, plan_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, task_description, execution_type, processor_type,
                 reference_id, plan_json),
            )
            conn.commit()
            return cur.lastrowid or 0

    def get_delegation_log(
        self, user_id: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Get delegation log entries."""
        with self._get_conn() as conn:
            if user_id:
                rows = conn.execute(
                    """SELECT * FROM delegation_log
                       WHERE user_id = ? ORDER BY created_at DESC LIMIT ?""",
                    (user_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM delegation_log ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

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
    password_hash TEXT,
    role TEXT DEFAULT 'user',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Cross-channel identity
CREATE TABLE IF NOT EXISTS user_channels (
    user_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    channel_user_id TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
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
    tool_call_id TEXT,
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

-- 7. Favorites
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

-- 10. Cron jobs + reminders
CREATE TABLE IF NOT EXISTS cron_jobs (
    job_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    cron_expr TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL,
    channel TEXT DEFAULT 'api',
    enabled INTEGER DEFAULT 1,
    run_at TEXT,
    agent_prompt TEXT,
    agent_tools TEXT,
    agent_model TEXT,
    notify_condition TEXT DEFAULT 'always',
    consecutive_failures INTEGER DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- 11. Cron execution log
CREATE TABLE IF NOT EXISTS cron_execution_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    result TEXT,
    status TEXT DEFAULT 'success',
    tokens_used INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0
);

-- 12. Reminders (standalone, no LLM)
CREATE TABLE IF NOT EXISTS reminders (
    reminder_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    message TEXT NOT NULL,
    channel TEXT DEFAULT 'telegram',
    run_at TEXT NOT NULL,
    cron_expr TEXT,
    status TEXT DEFAULT 'pending',
    retry_count INTEGER DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sent_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- 13. System events (background → agent communication)
CREATE TABLE IF NOT EXISTS system_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    source TEXT,
    event_type TEXT,
    payload TEXT,
    is_delivered BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- 14. Background tasks (subagent results)
CREATE TABLE IF NOT EXISTS background_tasks (
    task_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    parent_session TEXT,
    fallback_channel TEXT,
    task_description TEXT NOT NULL,
    status TEXT DEFAULT 'running',
    result TEXT,
    error TEXT,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- 15. API Keys
CREATE TABLE IF NOT EXISTS api_keys (
    key_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- 16. Delegation log (planner decision audit trail)
CREATE TABLE IF NOT EXISTS delegation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    task_description TEXT NOT NULL,
    execution_type TEXT NOT NULL,
    processor_type TEXT NOT NULL,
    reference_id TEXT,
    plan_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
CREATE INDEX IF NOT EXISTS idx_delegation_user ON delegation_log(user_id, created_at DESC);
"""
