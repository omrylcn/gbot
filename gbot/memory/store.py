"""SQLite-based memory store for GraphBot.

12 tables: users, user_channels, sessions, messages, agent_memory,
user_notes, favorites, preferences, background_tasks, task_executions,
system_events, api_keys.
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

    def __init__(self, db_path: str = "data/gbot.db"):
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
        conn.execute("UPDATE users SET role = 'member' WHERE role = 'user'")

        # Faz 21: Unified task table migration
        old_tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "cron_jobs" in old_tables:
            # Existing DB with old schema → migrate
            self._migrate_to_unified_tasks(conn, old_tables)
        elif "background_tasks" not in old_tables:
            # Fresh DB → create new tables
            self._create_task_tables(conn)
        else:
            # Already migrated — check if execution_type column exists
            bt_cols = {r[1] for r in conn.execute("PRAGMA table_info(background_tasks)").fetchall()}
            if "execution_type" not in bt_cols:
                # Old background_tasks schema without unified columns
                self._migrate_to_unified_tasks(conn, old_tables)

    _TASK_TABLES_SQL = """
        CREATE TABLE IF NOT EXISTS background_tasks (
            task_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            execution_type TEXT NOT NULL DEFAULT 'immediate',
            processor TEXT NOT NULL DEFAULT 'agent',
            message TEXT NOT NULL,
            channel TEXT DEFAULT 'api',
            cron_expr TEXT,
            run_at TEXT,
            enabled INTEGER DEFAULT 1,
            agent_prompt TEXT,
            agent_tools TEXT,
            agent_model TEXT,
            notify_condition TEXT DEFAULT 'always',
            plan_json TEXT,
            status TEXT DEFAULT 'pending',
            result TEXT,
            error TEXT,
            retry_count INTEGER DEFAULT 0,
            consecutive_failures INTEGER DEFAULT 0,
            last_error TEXT,
            parent_session TEXT,
            fallback_channel TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            sent_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_user
            ON background_tasks(user_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_tasks_status
            ON background_tasks(status, execution_type);

        CREATE TABLE IF NOT EXISTS task_executions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            execution_type TEXT,
            processor_type TEXT,
            status TEXT DEFAULT 'success',
            result TEXT,
            error TEXT,
            tokens_used INTEGER DEFAULT 0,
            duration_ms INTEGER DEFAULT 0,
            plan_json TEXT,
            reference_id TEXT,
            executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_executions_task
            ON task_executions(task_id, executed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_executions_user
            ON task_executions(user_id, executed_at DESC);
    """

    def _create_task_tables(self, conn) -> None:
        """Create unified task tables for fresh databases."""
        conn.executescript(self._TASK_TABLES_SQL)

    def _migrate_to_unified_tasks(self, conn, old_tables: set[str]) -> None:
        """Migrate cron_jobs + reminders + old background_tasks + delegation_log → unified tables."""
        logger.info("Migrating to unified background_tasks schema (Faz 21)...")

        # 1. Rename old background_tasks if it exists (different schema)
        if "background_tasks" in old_tables:
            conn.execute("ALTER TABLE background_tasks RENAME TO _bg_tasks_old")

        # 2. Create new unified tables
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS background_tasks (
                task_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                execution_type TEXT NOT NULL DEFAULT 'immediate',
                processor TEXT NOT NULL DEFAULT 'agent',
                message TEXT NOT NULL,
                channel TEXT DEFAULT 'api',
                cron_expr TEXT,
                run_at TEXT,
                enabled INTEGER DEFAULT 1,
                agent_prompt TEXT,
                agent_tools TEXT,
                agent_model TEXT,
                notify_condition TEXT DEFAULT 'always',
                plan_json TEXT,
                status TEXT DEFAULT 'pending',
                result TEXT,
                error TEXT,
                retry_count INTEGER DEFAULT 0,
                consecutive_failures INTEGER DEFAULT 0,
                last_error TEXT,
                parent_session TEXT,
                fallback_channel TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                sent_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_user
                ON background_tasks(user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_tasks_status
                ON background_tasks(status, execution_type);

            CREATE TABLE IF NOT EXISTS task_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                execution_type TEXT,
                processor_type TEXT,
                status TEXT DEFAULT 'success',
                result TEXT,
                error TEXT,
                tokens_used INTEGER DEFAULT 0,
                duration_ms INTEGER DEFAULT 0,
                plan_json TEXT,
                reference_id TEXT,
                executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_executions_task
                ON task_executions(task_id, executed_at DESC);
            CREATE INDEX IF NOT EXISTS idx_executions_user
                ON task_executions(user_id, executed_at DESC);
        """)

        # 3. Migrate cron_jobs → background_tasks
        conn.execute("""
            INSERT INTO background_tasks
                (task_id, user_id, message, execution_type, processor, channel,
                 cron_expr, enabled, agent_prompt, agent_tools, agent_model,
                 notify_condition, plan_json, status, consecutive_failures,
                 last_error, run_at, created_at)
            SELECT
                job_id, user_id, message,
                CASE WHEN notify_condition = 'notify_skip' THEN 'monitor'
                     ELSE 'recurring' END,
                COALESCE(processor, 'agent'),
                COALESCE(channel, 'api'),
                cron_expr, enabled, agent_prompt, agent_tools, agent_model,
                notify_condition, plan_json,
                CASE WHEN enabled THEN 'pending' ELSE 'paused' END,
                consecutive_failures, last_error, run_at, created_at
            FROM cron_jobs
        """)

        # 4. Migrate reminders → background_tasks
        conn.execute("""
            INSERT INTO background_tasks
                (task_id, user_id, message, execution_type, processor, channel,
                 cron_expr, run_at, status, retry_count, last_error,
                 agent_prompt, agent_tools, plan_json, created_at, sent_at)
            SELECT
                reminder_id, user_id, message,
                CASE WHEN cron_expr IS NOT NULL THEN 'recurring' ELSE 'delayed' END,
                COALESCE(processor, 'static'),
                COALESCE(channel, 'telegram'),
                cron_expr, run_at, status, retry_count, last_error,
                agent_prompt, agent_tools, plan_json, created_at, sent_at
            FROM reminders
        """)

        # 5. Migrate old background_tasks → new background_tasks
        if "_bg_tasks_old" in {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='_bg_tasks_old'"
            ).fetchall()
        }:
            conn.execute("""
                INSERT INTO background_tasks
                    (task_id, user_id, message, execution_type, processor,
                     status, result, error, parent_session, fallback_channel,
                     started_at, completed_at)
                SELECT
                    task_id, user_id, task_description, 'immediate', 'agent',
                    status, result, error, parent_session, fallback_channel,
                    started_at, completed_at
                FROM _bg_tasks_old
            """)
            conn.execute("DROP TABLE _bg_tasks_old")

        # 6. Migrate cron_execution_log → task_executions
        if "cron_execution_log" in old_tables:
            conn.execute("""
                INSERT INTO task_executions
                    (task_id, user_id, status, result, tokens_used,
                     duration_ms, executed_at)
                SELECT
                    cel.job_id,
                    COALESCE(bt.user_id, ''),
                    cel.status, cel.result, cel.tokens_used,
                    cel.duration_ms, cel.executed_at
                FROM cron_execution_log cel
                LEFT JOIN background_tasks bt ON bt.task_id = cel.job_id
            """)

        # 7. Migrate delegation_log → task_executions
        if "delegation_log" in old_tables:
            conn.execute("""
                INSERT INTO task_executions
                    (task_id, user_id, execution_type, processor_type,
                     reference_id, plan_json, status, result, executed_at)
                SELECT
                    COALESCE(reference_id, 'plan:' || id),
                    user_id, execution_type, processor_type,
                    reference_id, plan_json, 'planned',
                    task_description, created_at
                FROM delegation_log
            """)

        # 8. Drop old tables
        for table in ("cron_jobs", "cron_execution_log", "reminders", "delegation_log"):
            if table in old_tables:
                conn.execute(f"DROP TABLE IF EXISTS {table}")

        logger.info("Migration to unified tasks complete.")

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
    # BACKGROUND TASKS (unified: immediate/delayed/recurring/monitor)
    # ════════════════════════════════════════════════════════════

    def create_task(
        self,
        task_id: str,
        user_id: str,
        message: str,
        execution_type: str = "immediate",
        processor: str = "agent",
        channel: str = "api",
        *,
        cron_expr: str | None = None,
        run_at: str | None = None,
        enabled: bool = True,
        agent_prompt: str | None = None,
        agent_tools: str | None = None,
        agent_model: str | None = None,
        notify_condition: str = "always",
        plan_json: str | None = None,
        status: str = "pending",
        parent_session: str | None = None,
        fallback_channel: str | None = None,
    ) -> None:
        """Insert a new task into background_tasks."""
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO background_tasks
                   (task_id, user_id, message, execution_type, processor, channel,
                    cron_expr, run_at, enabled, agent_prompt, agent_tools,
                    agent_model, notify_condition, plan_json, status,
                    parent_session, fallback_channel)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (task_id, user_id, message, execution_type, processor, channel,
                 cron_expr, run_at, int(enabled), agent_prompt, agent_tools,
                 agent_model, notify_condition, plan_json, status,
                 parent_session, fallback_channel),
            )
            conn.commit()

    def get_tasks(
        self,
        user_id: str | None = None,
        execution_type: str | None = None,
        status: str | None = None,
        enabled: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Query tasks with optional filters."""
        clauses, params = [], []
        if user_id:
            clauses.append("user_id = ?")
            params.append(user_id)
        if execution_type:
            clauses.append("execution_type = ?")
            params.append(execution_type)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if enabled is not None:
            clauses.append("enabled = ?")
            params.append(int(enabled))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._get_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM background_tasks{where} ORDER BY created_at DESC",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Get a single task by ID."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM background_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return dict(row) if row else None

    def update_task(self, task_id: str, **fields: Any) -> None:
        """Update specific fields on a task."""
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [task_id]
        with self._get_conn() as conn:
            conn.execute(
                f"UPDATE background_tasks SET {sets} WHERE task_id = ?", vals
            )
            conn.commit()

    def cancel_task(self, task_id: str) -> bool:
        """Set status='cancelled' for pending/running tasks. Returns True if changed."""
        with self._get_conn() as conn:
            cur = conn.execute(
                """UPDATE background_tasks SET status = 'cancelled'
                   WHERE task_id = ? AND status IN ('pending', 'running')""",
                (task_id,),
            )
            conn.commit()
        return cur.rowcount > 0

    def delete_task(self, task_id: str) -> None:
        """Hard-delete a task row."""
        with self._get_conn() as conn:
            conn.execute(
                "DELETE FROM background_tasks WHERE task_id = ?", (task_id,)
            )
            conn.commit()

    def increment_task_failures(self, task_id: str, error: str) -> int:
        """Increment consecutive_failures, record last_error. Returns new count."""
        with self._get_conn() as conn:
            conn.execute(
                """UPDATE background_tasks
                   SET consecutive_failures = consecutive_failures + 1,
                       last_error = ?
                   WHERE task_id = ?""",
                (error, task_id),
            )
            conn.commit()
            row = conn.execute(
                "SELECT consecutive_failures FROM background_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return row["consecutive_failures"] if row else 0

    def reset_task_failures(self, task_id: str) -> None:
        """Reset consecutive_failures to 0."""
        with self._get_conn() as conn:
            conn.execute(
                """UPDATE background_tasks
                   SET consecutive_failures = 0, last_error = NULL
                   WHERE task_id = ?""",
                (task_id,),
            )
            conn.commit()

    # ── Task executions (unified audit log) ────────────────────

    def log_execution(
        self,
        task_id: str,
        user_id: str,
        *,
        execution_type: str | None = None,
        processor_type: str | None = None,
        status: str = "success",
        result: str | None = None,
        error: str | None = None,
        tokens_used: int = 0,
        duration_ms: int = 0,
        plan_json: str | None = None,
        reference_id: str | None = None,
    ) -> int:
        """Record a task execution or delegation decision. Returns row ID."""
        with self._get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO task_executions
                   (task_id, user_id, execution_type, processor_type,
                    status, result, error, tokens_used, duration_ms,
                    plan_json, reference_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (task_id, user_id, execution_type, processor_type,
                 status, result, error, tokens_used, duration_ms,
                 plan_json, reference_id),
            )
            conn.commit()
            return cur.lastrowid or 0

    def get_executions(
        self,
        task_id: str | None = None,
        user_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Get execution log entries, filtered by task_id or user_id."""
        clauses, params = [], []
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        if user_id:
            clauses.append("user_id = ?")
            params.append(user_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        with self._get_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM task_executions{where} ORDER BY executed_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Legacy wrappers (thin compatibility layer) ─────────────
    # These map old method names to new unified API.
    # Will be removed after all callers are updated.

    def add_cron_job(self, job_id, user_id, cron_expr, message, channel="api",
                     enabled=True, agent_prompt=None, agent_tools=None,
                     agent_model=None, notify_condition="always",
                     processor="agent", plan_json=None):
        exec_type = "monitor" if notify_condition == "notify_skip" else "recurring"
        self.create_task(
            job_id, user_id, message, execution_type=exec_type,
            processor=processor, channel=channel, cron_expr=cron_expr,
            enabled=enabled, agent_prompt=agent_prompt, agent_tools=agent_tools,
            agent_model=agent_model, notify_condition=notify_condition,
            plan_json=plan_json,
        )

    def get_cron_jobs(self, user_id=None):
        types = ("recurring", "monitor")
        tasks = self.get_tasks(user_id=user_id)
        result = []
        for t in tasks:
            if t["execution_type"] in types:
                t["job_id"] = t["task_id"]
                result.append(t)
        return result

    def remove_cron_job(self, job_id):
        self.delete_task(job_id)

    def add_reminder(self, reminder_id, user_id, run_at, message,
                     channel="telegram", cron_expr=None, agent_prompt=None,
                     agent_tools=None, processor="static", plan_json=None):
        exec_type = "recurring" if cron_expr else "delayed"
        self.create_task(
            reminder_id, user_id, message, execution_type=exec_type,
            processor=processor, channel=channel, cron_expr=cron_expr,
            run_at=run_at, agent_prompt=agent_prompt, agent_tools=agent_tools,
            plan_json=plan_json,
        )

    def get_pending_reminders(self, user_id=None):
        tasks = self.get_tasks(user_id=user_id, status="pending")
        result = []
        for t in tasks:
            if t["execution_type"] in ("delayed", "recurring") and t.get("run_at"):
                t["reminder_id"] = t["task_id"]
                result.append(t)
        return result

    def mark_reminder_sent(self, reminder_id):
        self.update_task(reminder_id, status="sent", sent_at="now")

    def mark_reminder_failed(self, reminder_id, error):
        task = self.get_task(reminder_id)
        if not task:
            return
        retry = task.get("retry_count", 0) + 1
        new_status = "failed" if retry >= 3 else "pending"
        self.update_task(reminder_id, retry_count=retry, last_error=error, status=new_status)

    def cancel_reminder(self, reminder_id):
        return self.cancel_task(reminder_id)

    def remove_reminder(self, reminder_id):
        self.delete_task(reminder_id)

    def log_cron_execution(self, job_id, result, status="success",
                           tokens_used=0, duration_ms=0):
        task = self.get_task(job_id)
        user_id = task["user_id"] if task else ""
        self.log_execution(
            job_id, user_id, status=status, result=result,
            tokens_used=tokens_used, duration_ms=duration_ms,
        )

    def get_cron_execution_log(self, job_id, limit=20):
        return self.get_executions(task_id=job_id, limit=limit)

    def increment_cron_failures(self, job_id, error):
        return self.increment_task_failures(job_id, error)

    def reset_cron_failures(self, job_id):
        self.reset_task_failures(job_id)

    # ── System events (background → agent) ───────────────────

    def add_system_event(
        self,
        user_id: str,
        source: str,
        event_type: str,
        payload: str,
        channel: str | None = None,
        session_id: str | None = None,
    ) -> int:
        """Create a system event. Returns the event ID."""
        with self._get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO system_events
                   (user_id, channel, session_id, source, event_type, payload)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, channel, session_id, source, event_type, payload),
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

    # ── Legacy wrappers: background tasks + delegation ────────
    # These map old method names to new unified API.

    def create_background_task(self, task_id, user_id, description,
                               parent_session=None, fallback_channel=None):
        self.create_task(
            task_id, user_id, description, execution_type="immediate",
            processor="agent", status="running",
            parent_session=parent_session, fallback_channel=fallback_channel,
        )

    def complete_background_task(self, task_id, result):
        self.update_task(task_id, status="completed", result=result)

    def fail_background_task(self, task_id, error):
        self.update_task(task_id, status="failed", error=error)

    def get_background_task(self, task_id):
        return self.get_task(task_id)

    def log_delegation(self, user_id, task_description, execution_type,
                       processor_type, reference_id=None, plan_json=None):
        return self.log_execution(
            reference_id or f"plan:{user_id}", user_id,
            execution_type=execution_type, processor_type=processor_type,
            status="planned", result=task_description,
            plan_json=plan_json, reference_id=reference_id,
        )

    def get_delegation_log(self, user_id=None, limit=20):
        execs = self.get_executions(user_id=user_id, limit=limit)
        return [e for e in execs if e.get("status") == "planned"]

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

-- 10. System events (delivery queue for API/WS channels)
CREATE TABLE IF NOT EXISTS system_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    channel TEXT,
    session_id TEXT,
    source TEXT,
    event_type TEXT,
    payload TEXT,
    is_delivered BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- 13. API Keys
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

"""
