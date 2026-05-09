"""SQLite-based memory store for GraphBot.

12 tables: users, user_channels, sessions, messages, agent_memory,
user_notes (unified: notes+prefs+favs), memory_facts, memory_processing_log,
background_tasks, task_executions, system_events, api_keys.
"""

from __future__ import annotations

import json
import sqlite3
import uuid

import sqlite_vec
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
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
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

        old_tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        # Faz 21: Unified task table migration
        if "cron_jobs" in old_tables:
            self._migrate_to_unified_tasks(conn, old_tables)
        elif "background_tasks" not in old_tables:
            self._create_task_tables(conn)
        else:
            bt_cols = {r[1] for r in conn.execute("PRAGMA table_info(background_tasks)").fetchall()}
            if "execution_type" not in bt_cols:
                self._migrate_to_unified_tasks(conn, old_tables)

        # Faz 22D: Backlinks + Entity Pages migration. Run BEFORE
        # _create_memory_tables so canonical_* columns exist on legacy
        # memory_relations before we try to add indexes that reference them.
        # Idempotent — guarded by user_version pragma.
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 22 and "memory_relations" in old_tables:
            self._migrate_22d(conn)
            conn.execute("PRAGMA user_version = 22")

        # Faz 22: Memory tables (always ensure they exist)
        memory_tables = {"memory_facts", "vec_memory_facts", "memory_relations", "memory_processing_log"}
        if not memory_tables.issubset(old_tables):
            self._create_memory_tables(conn)

        # If memory_relations didn't exist before (fresh DB), the bump above
        # was skipped. Set user_version now so we don't try to remigrate.
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 22:
            # Ensure aliases/pages tables on a fresh DB built from scratch.
            self._migrate_22d(conn)
            conn.execute("PRAGMA user_version = 22")

        # Faz 22G: 4-state lifecycle on memory_facts.
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 23:
            self._migrate_22g(conn)
            conn.execute("PRAGMA user_version = 23")

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

    _MEMORY_TABLES_SQL = """
        CREATE TABLE IF NOT EXISTS memory_facts (
            fact_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            content TEXT NOT NULL,
            fact_type TEXT NOT NULL DEFAULT 'semantic',
            source TEXT DEFAULT 'extraction',
            source_session TEXT,
            source_channel TEXT,
            confidence REAL DEFAULT 1.0,
            importance REAL DEFAULT 0.5,
            access_count INTEGER DEFAULT 0,
            valid_from TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            valid_until TIMESTAMP,
            superseded_by TEXT,
            keywords TEXT,
            category TEXT,
            embedding BLOB,
            -- Faz 22G: explicit lifecycle state.
            -- ACTIVE   = fresh, full retrieval weight
            -- WEAK     = post-fade decay, retrieved at lower priority
            -- INHIBITED= temporarily excluded; auto-restores after
            --            inhibited_until expires (default 7d undo window)
            -- ARCHIVED = invalidated, retrieval-excluded, audit-only
            state TEXT NOT NULL DEFAULT 'active'
                CHECK (state IN ('active','weak','inhibited','archived')),
            inhibited_until TIMESTAMP,
            last_accessed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_facts_user
            ON memory_facts(user_id, fact_type);
        CREATE INDEX IF NOT EXISTS idx_facts_valid
            ON memory_facts(user_id, valid_until);
        CREATE INDEX IF NOT EXISTS idx_facts_state
            ON memory_facts(user_id, state);

        CREATE VIRTUAL TABLE IF NOT EXISTS vec_memory_facts
            USING vec0(embedding float[3072] distance_metric=cosine);

        CREATE TABLE IF NOT EXISTS memory_relations (
            relation_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            source_entity TEXT NOT NULL,
            relation TEXT NOT NULL,
            target_entity TEXT NOT NULL,
            canonical_source TEXT,
            canonical_target TEXT,
            confidence REAL DEFAULT 1.0,
            valid_from TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            valid_until TIMESTAMP,
            source_fact TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_relations_user
            ON memory_relations(user_id);
        CREATE INDEX IF NOT EXISTS idx_relations_source
            ON memory_relations(source_entity);
        CREATE INDEX IF NOT EXISTS idx_relations_target
            ON memory_relations(user_id, target_entity);
        CREATE INDEX IF NOT EXISTS idx_relations_canonical_source
            ON memory_relations(user_id, canonical_source);
        CREATE INDEX IF NOT EXISTS idx_relations_canonical_target
            ON memory_relations(user_id, canonical_target);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_relations_active
            ON memory_relations(user_id, source_entity, relation, target_entity)
            WHERE valid_until IS NULL;

        CREATE TABLE IF NOT EXISTS memory_processing_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            session_id TEXT,
            trigger TEXT,
            facts_extracted INTEGER DEFAULT 0,
            facts_added INTEGER DEFAULT 0,
            facts_updated INTEGER DEFAULT 0,
            facts_invalidated INTEGER DEFAULT 0,
            duration_ms INTEGER DEFAULT 0,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS memory_entity_aliases (
            user_id TEXT NOT NULL,
            surface_form TEXT NOT NULL,
            canonical_form TEXT NOT NULL,
            source TEXT DEFAULT 'auto',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, surface_form),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_aliases_canonical
            ON memory_entity_aliases(user_id, canonical_form);

        CREATE TABLE IF NOT EXISTS memory_entity_pages (
            page_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            entity_canonical TEXT NOT NULL,
            entity_surface_forms TEXT,
            content_md TEXT NOT NULL,
            source_fact_ids TEXT,
            source_relation_ids TEXT,
            fact_count INTEGER DEFAULT 0,
            relation_count INTEGER DEFAULT 0,
            version INTEGER DEFAULT 1,
            stale INTEGER DEFAULT 0,
            last_compiled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_accessed_at TIMESTAMP,
            access_count INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_entity_pages_user_entity
            ON memory_entity_pages(user_id, entity_canonical);
        CREATE INDEX IF NOT EXISTS idx_entity_pages_stale
            ON memory_entity_pages(user_id, stale);
    """

    def _create_task_tables(self, conn) -> None:
        """Create unified task tables for fresh databases."""
        conn.executescript(self._TASK_TABLES_SQL)

    def _create_memory_tables(self, conn) -> None:
        """Create memory tables for fresh or migrated databases."""
        conn.executescript(self._MEMORY_TABLES_SQL)

    def _migrate_22d(self, conn) -> None:
        """Faz 22D — backlinks revival + entity pages.

        Adds canonical_source/canonical_target columns to memory_relations,
        deduplicates existing rows, creates partial UNIQUE index, and creates
        new tables (memory_entity_aliases, memory_entity_pages). Idempotent.
        """
        rel_cols = {r[1] for r in conn.execute("PRAGMA table_info(memory_relations)").fetchall()}
        if "canonical_source" not in rel_cols:
            conn.execute("ALTER TABLE memory_relations ADD COLUMN canonical_source TEXT")
        if "canonical_target" not in rel_cols:
            conn.execute("ALTER TABLE memory_relations ADD COLUMN canonical_target TEXT")

        # Dedup live rows: keep the oldest rowid per (user, source, rel, target)
        conn.execute(
            """DELETE FROM memory_relations
               WHERE valid_until IS NULL
                 AND rowid NOT IN (
                   SELECT MIN(rowid) FROM memory_relations
                   WHERE valid_until IS NULL
                   GROUP BY user_id, source_entity, relation, target_entity
                 )"""
        )

        # Partial UNIQUE index — only over live rows so re-asserts after
        # invalidate stay legal.
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS uq_relations_active
                   ON memory_relations(user_id, source_entity, relation, target_entity)
                   WHERE valid_until IS NULL"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_relations_target ON memory_relations(user_id, target_entity)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_relations_canonical_source ON memory_relations(user_id, canonical_source)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_relations_canonical_target ON memory_relations(user_id, canonical_target)"
        )

        # New tables — create if missing.
        conn.execute(
            """CREATE TABLE IF NOT EXISTS memory_entity_aliases (
                   user_id TEXT NOT NULL,
                   surface_form TEXT NOT NULL,
                   canonical_form TEXT NOT NULL,
                   source TEXT DEFAULT 'auto',
                   created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                   PRIMARY KEY (user_id, surface_form),
                   FOREIGN KEY (user_id) REFERENCES users(user_id)
               )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_aliases_canonical ON memory_entity_aliases(user_id, canonical_form)"
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS memory_entity_pages (
                   page_id TEXT PRIMARY KEY,
                   user_id TEXT NOT NULL,
                   entity_canonical TEXT NOT NULL,
                   entity_surface_forms TEXT,
                   content_md TEXT NOT NULL,
                   source_fact_ids TEXT,
                   source_relation_ids TEXT,
                   fact_count INTEGER DEFAULT 0,
                   relation_count INTEGER DEFAULT 0,
                   version INTEGER DEFAULT 1,
                   stale INTEGER DEFAULT 0,
                   last_compiled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                   last_accessed_at TIMESTAMP,
                   access_count INTEGER DEFAULT 0,
                   FOREIGN KEY (user_id) REFERENCES users(user_id)
               )"""
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_entity_pages_user_entity ON memory_entity_pages(user_id, entity_canonical)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entity_pages_stale ON memory_entity_pages(user_id, stale)"
        )

        logger.info("Faz 22D schema migration applied (relations dedup + canonical cols + entity pages/aliases)")

    def _migrate_22g(self, conn) -> None:
        """Faz 22G — explicit lifecycle state on memory_facts.

        Adds ``state`` (active / weak / inhibited / archived),
        ``inhibited_until``, and ``last_accessed_at`` columns. Backfills
        existing rows from ``valid_until`` and ``importance`` so the
        explicit state matches the prior implicit logic. Idempotent.
        """
        cols = {
            r[1]
            for r in conn.execute("PRAGMA table_info(memory_facts)").fetchall()
        }

        if "state" not in cols:
            conn.execute(
                "ALTER TABLE memory_facts ADD COLUMN state TEXT "
                "NOT NULL DEFAULT 'active'"
            )
        if "inhibited_until" not in cols:
            conn.execute(
                "ALTER TABLE memory_facts ADD COLUMN inhibited_until TIMESTAMP"
            )
        if "last_accessed_at" not in cols:
            conn.execute(
                "ALTER TABLE memory_facts ADD COLUMN last_accessed_at TIMESTAMP"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_facts_state "
            "ON memory_facts(user_id, state)"
        )

        # Backfill: derive state from prior implicit signals.
        # Only updates rows still at the default 'active' to avoid
        # clobbering anything a later run already promoted.
        conn.execute(
            """UPDATE memory_facts SET state = CASE
                   WHEN valid_until IS NOT NULL THEN 'archived'
                   WHEN importance < 0.3 THEN 'weak'
                   ELSE 'active'
               END
               WHERE state = 'active'"""
        )

        logger.info(
            "Faz 22G schema migration applied (memory_facts.state + "
            "inhibited_until + last_accessed_at; existing rows backfilled)"
        )

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

    def get_last_session_meta(self, user_id: str) -> dict | None:
        """Faz 22H — last closed session's summary + timestamps so the
        ContextBuilder can render '(12 gün önce)' on the Previous
        Conversation header.
        """
        with self._get_conn() as conn:
            row = conn.execute(
                """SELECT summary, started_at, ended_at FROM sessions
                   WHERE user_id = ? AND ended_at IS NOT NULL AND summary IS NOT NULL
                   ORDER BY ended_at DESC LIMIT 1""",
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

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
                """INSERT INTO user_notes (user_id, note_type, content, source)
                   VALUES (?, 'note', ?, ?)""",
                (user_id, note, source),
            )
            conn.commit()
            return cursor.lastrowid or 0

    def get_notes(self, user_id: str, limit: int = 50) -> list[str]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT content FROM user_notes
                   WHERE user_id = ? AND note_type = 'note'
                   ORDER BY created_at DESC LIMIT ?""",
                (user_id, limit),
            ).fetchall()
        return [r["content"] for r in rows]

    def get_notes_with_ids(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT id, content, source, created_at FROM user_notes
                   WHERE user_id = ? AND note_type = 'note'
                   ORDER BY created_at DESC LIMIT ?""",
                (user_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_note(self, note_id: int) -> None:
        with self._get_conn() as conn:
            conn.execute("DELETE FROM user_notes WHERE id = ?", (note_id,))
            conn.commit()

    # ════════════════════════════════════════════════════════════
    # FAVORITES
    # ════════════════════════════════════════════════════════════

    def add_favorite(self, user_id: str, item_id: str, item_title: str) -> None:
        self.get_or_create_user(user_id)
        meta = json.dumps({"item_id": item_id})
        with self._get_conn() as conn:
            # Remove existing favorite with same item_id, then insert
            conn.execute(
                """DELETE FROM user_notes
                   WHERE user_id = ? AND note_type = 'favorite'
                   AND json_extract(metadata, '$.item_id') = ?""",
                (user_id, item_id),
            )
            conn.execute(
                """INSERT INTO user_notes
                   (user_id, note_type, content, metadata, source)
                   VALUES (?, 'favorite', ?, ?, 'conversation')""",
                (user_id, item_title, meta),
            )
            conn.commit()

    def remove_favorite(self, user_id: str, item_id: str) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """DELETE FROM user_notes
                   WHERE user_id = ? AND note_type = 'favorite'
                   AND json_extract(metadata, '$.item_id') = ?""",
                (user_id, item_id),
            )
            conn.commit()

    def get_favorites(self, user_id: str) -> list[dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT content, metadata, created_at FROM user_notes
                   WHERE user_id = ? AND note_type = 'favorite'
                   ORDER BY created_at DESC""",
                (user_id,),
            ).fetchall()
        result = []
        for r in rows:
            meta = json.loads(r["metadata"]) if r["metadata"] else {}
            result.append({
                "item_id": meta.get("item_id", ""),
                "item_title": r["content"],
                "added_at": r["created_at"],
            })
        return result

    def is_favorite(self, user_id: str, item_id: str) -> bool:
        with self._get_conn() as conn:
            return (
                conn.execute(
                    """SELECT 1 FROM user_notes
                       WHERE user_id = ? AND note_type = 'favorite'
                       AND json_extract(metadata, '$.item_id') = ?""",
                    (user_id, item_id),
                ).fetchone()
                is not None
            )

    # ════════════════════════════════════════════════════════════
    # PREFERENCES (flexible JSON blob)
    # ════════════════════════════════════════════════════════════

    def get_preferences(self, user_id: str) -> dict[str, Any]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT key, content FROM user_notes
                   WHERE user_id = ? AND note_type = 'preference' AND key IS NOT NULL""",
                (user_id,),
            ).fetchall()
        return {r["key"]: r["content"] for r in rows}

    def update_preferences(self, user_id: str, data: dict[str, Any]) -> None:
        """Merge new data into existing preferences (one row per key)."""
        self.get_or_create_user(user_id)
        with self._get_conn() as conn:
            for k, v in data.items():
                # Partial unique index (user_id, key) WHERE note_type='preference'
                # allows INSERT OR REPLACE to work correctly
                conn.execute(
                    """INSERT INTO user_notes (user_id, note_type, content, key, source)
                       VALUES (?, 'preference', ?, ?, 'extraction')
                       ON CONFLICT(user_id, key) WHERE note_type = 'preference'
                       DO UPDATE SET content = excluded.content,
                                     created_at = CURRENT_TIMESTAMP""",
                    (user_id, str(v), k),
                )
            conn.commit()

    def remove_preference(self, user_id: str, key: str) -> bool:
        """Remove a single preference key. Returns True if removed."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                """DELETE FROM user_notes
                   WHERE user_id = ? AND note_type = 'preference' AND key = ?""",
                (user_id, key),
            )
            conn.commit()
            return cursor.rowcount > 0

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
    # MEMORY FACTS (extracted knowledge)
    # ════════════════════════════════════════════════════════════

    def add_fact(
        self,
        fact_id: str,
        user_id: str,
        content: str,
        fact_type: str = "semantic",
        source: str = "extraction",
        source_session: str | None = None,
        source_channel: str | None = None,
        confidence: float = 1.0,
        importance: float = 0.5,
        keywords: list[str] | None = None,
        category: str | None = None,
        embedding: list[float] | None = None,
    ) -> None:
        with self._get_conn() as conn:
            cursor = conn.execute(
                """INSERT INTO memory_facts
                   (fact_id, user_id, content, fact_type, source,
                    source_session, source_channel, confidence, importance,
                    keywords, category)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    fact_id, user_id, content, fact_type, source,
                    source_session, source_channel, confidence, importance,
                    json.dumps(keywords) if keywords else None,
                    category,
                ),
            )
            # Store embedding in vec table (same transaction)
            if embedding:
                rowid = cursor.lastrowid
                conn.execute(
                    "DELETE FROM vec_memory_facts WHERE rowid = ?", (rowid,)
                )
                conn.execute(
                    "INSERT INTO vec_memory_facts(rowid, embedding) VALUES (?, ?)",
                    (rowid, json.dumps(embedding)),
                )
            conn.commit()

    def get_facts(
        self,
        user_id: str,
        fact_type: str | None = None,
        valid_only: bool = True,
        state: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Fetch facts.

        ``state`` (Faz 22G) filters the explicit lifecycle state —
        'active' / 'weak' / 'inhibited' / 'archived'. ``valid_only``
        keeps the legacy ``valid_until IS NULL`` gate; passing both
        is fine (the predicates are independent).
        """
        conditions = ["user_id = ?"]
        params: list[Any] = [user_id]
        if fact_type:
            conditions.append("fact_type = ?")
            params.append(fact_type)
        if valid_only:
            conditions.append("valid_until IS NULL")
        if state:
            conditions.append("state = ?")
            params.append(state)
        params.append(limit)
        where = " AND ".join(conditions)
        with self._get_conn() as conn:
            rows = conn.execute(
                f"""SELECT * FROM memory_facts
                    WHERE {where}
                    ORDER BY importance DESC, created_at DESC
                    LIMIT ?""",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def get_fact(self, fact_id: str) -> dict[str, Any] | None:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM memory_facts WHERE fact_id = ?", (fact_id,)
            ).fetchone()
        return dict(row) if row else None

    def update_fact(self, fact_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = "CURRENT_TIMESTAMP"
        sets = []
        params = []
        for k, v in fields.items():
            if v == "CURRENT_TIMESTAMP":
                sets.append(f"{k} = CURRENT_TIMESTAMP")
            else:
                sets.append(f"{k} = ?")
                params.append(v)
        params.append(fact_id)
        with self._get_conn() as conn:
            conn.execute(
                f"UPDATE memory_facts SET {', '.join(sets)} WHERE fact_id = ?",
                params,
            )
            conn.commit()

    def invalidate_fact(
        self, fact_id: str, superseded_by: str | None = None
    ) -> None:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT rowid, user_id FROM memory_facts WHERE fact_id = ?",
                (fact_id,),
            ).fetchone()
            conn.execute(
                """UPDATE memory_facts
                   SET valid_until = CURRENT_TIMESTAMP,
                       state = 'archived',
                       superseded_by = ?,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE fact_id = ?""",
                (superseded_by, fact_id),
            )
            if row:
                conn.execute(
                    "DELETE FROM vec_memory_facts WHERE rowid = ?", (row[0],)
                )
                # Faz 22D Step 7 — mark dependent entity pages stale
                # so the compiler refreshes them on the next cycle.
                pattern = f'%"{fact_id}"%'
                conn.execute(
                    """UPDATE memory_entity_pages
                           SET stale = 1
                           WHERE user_id = ? AND source_fact_ids LIKE ?""",
                    (row["user_id"], pattern),
                )
            conn.commit()

    def inhibit_fact(
        self, fact_id: str, hold_days: int = 7
    ) -> bool:
        """Mark a fact INHIBITED — excluded from retrieval until
        ``hold_days`` pass. Auto-restores via ``apply_decay`` once the
        hold lapses. Returns False if the fact wasn't found.
        """
        with self._get_conn() as conn:
            cur = conn.execute(
                """UPDATE memory_facts
                   SET state = 'inhibited',
                       inhibited_until = datetime('now', '+' || ? || ' days'),
                       updated_at = CURRENT_TIMESTAMP
                   WHERE fact_id = ? AND valid_until IS NULL""",
                (int(hold_days), fact_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def restore_fact(self, fact_id: str) -> bool:
        """INHIBITED → ACTIVE. No effect on archived facts. Returns
        False if the fact isn't currently inhibited.
        """
        with self._get_conn() as conn:
            cur = conn.execute(
                """UPDATE memory_facts
                   SET state = 'active', inhibited_until = NULL,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE fact_id = ? AND state = 'inhibited'""",
                (fact_id,),
            )
            conn.commit()
            return cur.rowcount > 0

    def increment_fact_access(self, fact_id: str) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """UPDATE memory_facts
                   SET access_count = access_count + 1
                   WHERE fact_id = ?""",
                (fact_id,),
            )
            conn.commit()

    def batch_increment_access(self, fact_ids: list[str]) -> None:
        """Increment access_count for multiple facts in one query."""
        if not fact_ids:
            return
        placeholders = ",".join("?" for _ in fact_ids)
        with self._get_conn() as conn:
            conn.execute(
                f"""UPDATE memory_facts
                    SET access_count = access_count + 1
                    WHERE fact_id IN ({placeholders})""",
                fact_ids,
            )
            conn.commit()

    # Default per-type half-lives (days). Episodic facts age fastest;
    # preferences and semantic identity facts age slowest. Override via
    # ``apply_decay(rates=...)``.
    _DEFAULT_DECAY_RATES: dict[str, dict[str, float]] = {
        # type        fade_days  fade_factor  archive_days  archive_factor
        "episodic":   {"fade_days": 14,  "fade_factor": 0.7,  "archive_days": 60,  "archive_factor": 0.4},
        "procedural": {"fade_days": 60,  "fade_factor": 0.85, "archive_days": 180, "archive_factor": 0.6},
        "semantic":   {"fade_days": 90,  "fade_factor": 0.85, "archive_days": 365, "archive_factor": 0.6},
        "preference": {"fade_days": 120, "fade_factor": 0.9,  "archive_days": 365, "archive_factor": 0.7},
        # Faz 22G — communication style is the slowest-changing memory:
        # how someone writes shifts over months/years, not days.
        "style":      {"fade_days": 180, "fade_factor": 0.92, "archive_days": 540, "archive_factor": 0.75},
    }

    def apply_decay(
        self,
        user_id: str,
        rates: dict[str, dict[str, float]] | None = None,
        archive_threshold: float = 0.1,
    ) -> dict[str, int]:
        """Decrease importance of old, rarely-accessed facts.

        Type-aware: each ``fact_type`` gets its own fade/archive timeline
        (Faz 22D Step 11). Episodic facts age fast (good — yesterday's
        events lose value quickly); preferences age slowly (a vegetarian
        usually stays vegetarian).

        Parameters
        ----------
        rates : dict | None
            Override the default per-type rates. Each entry needs:
            ``fade_days``, ``fade_factor``, ``archive_days``, ``archive_factor``.
            Missing types fall back to defaults.
        archive_threshold : float
            Facts whose importance drops below this get ``valid_until``
            set, removing them from the active set.

        Returns
        -------
        dict with keys ``faded``, ``archived``, ``by_type`` (per-type counts).
        """
        rates = rates or self._DEFAULT_DECAY_RATES
        per_type: dict[str, int] = {}
        total_faded = 0

        with self._get_conn() as conn:
            for fact_type, params in rates.items():
                # Stage 1 fade — ACTIVE rows past fade_days drop importance
                # and flip to WEAK.
                conn.execute(
                    """UPDATE memory_facts
                       SET importance = importance * ?,
                           state = 'weak',
                           updated_at = CURRENT_TIMESTAMP
                       WHERE user_id = ? AND valid_until IS NULL
                         AND fact_type = ?
                         AND state = 'active'
                         AND access_count = 0
                         AND julianday('now') - julianday(created_at) > ?""",
                    (
                        params["fade_factor"], user_id, fact_type,
                        params["fade_days"],
                    ),
                )
                f1 = conn.execute("SELECT changes()").fetchone()[0]

                # Stage 2 deeper fade — WEAK rows past archive_days
                # take a second hit; they archive below if importance drops.
                conn.execute(
                    """UPDATE memory_facts
                       SET importance = importance * ?,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE user_id = ? AND valid_until IS NULL
                         AND fact_type = ?
                         AND state IN ('weak', 'active')
                         AND access_count = 0
                         AND julianday('now') - julianday(created_at) > ?""",
                    (
                        params["archive_factor"], user_id, fact_type,
                        params["archive_days"],
                    ),
                )
                f2 = conn.execute("SELECT changes()").fetchone()[0]

                per_type[fact_type] = f1 + f2
                total_faded += f1 + f2

            # Archive — applies to all types uniformly via importance threshold.
            # Set both valid_until (legacy gate) and state='archived' (Faz 22G).
            conn.execute(
                """UPDATE memory_facts
                   SET valid_until = CURRENT_TIMESTAMP,
                       state = 'archived',
                       updated_at = CURRENT_TIMESTAMP
                   WHERE user_id = ? AND valid_until IS NULL
                     AND importance < ?""",
                (user_id, archive_threshold),
            )
            archived = conn.execute("SELECT changes()").fetchone()[0]

            # Faz 22G: auto-restore — INHIBITED rows whose hold expired
            # come back as ACTIVE.
            conn.execute(
                """UPDATE memory_facts
                   SET state = 'active', inhibited_until = NULL,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE user_id = ? AND state = 'inhibited'
                     AND inhibited_until IS NOT NULL
                     AND inhibited_until <= CURRENT_TIMESTAMP""",
                (user_id,),
            )
            restored = conn.execute("SELECT changes()").fetchone()[0]
            conn.commit()

        return {
            "faded": total_faded,
            "archived": archived,
            "restored": restored,
            "by_type": per_type,
        }

    def search_similar_facts(
        self,
        user_id: str,
        query_embedding: list[float],
        top_k: int = 5,
        max_distance: float | None = None,
    ) -> list[dict[str, Any]]:
        """Semantic similarity search — user-scoped, valid facts only.

        Uses CTE + k= syntax for sqlite-vec compatibility.
        distance_metric=cosine: 0.0 = identical, 2.0 = opposite.

        Parameters
        ----------
        max_distance : float | None
            Drop candidates with cosine distance above this threshold.
            Applied inside the CTE so the rerank pool never sees noise.
            Use ``None`` (default) for no gate; ``0.45`` is a sensible
            relevance cutoff for gemini-embedding-001.
        """
        with self._get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM vec_memory_facts"
            ).fetchone()[0]
            if count == 0:
                return []
            params: list[Any] = [json.dumps(query_embedding), top_k * 3, user_id]
            distance_filter = ""
            if max_distance is not None:
                distance_filter = "AND knn.distance <= ?"
                params.append(max_distance)
            # Faz 22G: ACTIVE + WEAK make it into retrieval; INHIBITED is
            # excluded until ``inhibited_until`` lapses; ARCHIVED is never
            # retrieved (audit-only).
            rows = conn.execute(
                f"""WITH knn AS (
                        SELECT rowid, distance
                        FROM vec_memory_facts
                        WHERE embedding MATCH ? AND k = ?
                    )
                    SELECT f.*, knn.distance
                    FROM knn
                    JOIN memory_facts f ON f.rowid = knn.rowid
                    WHERE f.user_id = ?
                          AND f.valid_until IS NULL
                          AND f.state IN ('active', 'weak')
                          AND (f.inhibited_until IS NULL
                               OR f.inhibited_until < CURRENT_TIMESTAMP)
                          {distance_filter}
                    ORDER BY knn.distance""",
                params,
            ).fetchall()
        return [dict(r) for r in rows[:top_k]]

    def get_fact_stats(self, user_id: str) -> dict[str, Any]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT fact_type, COUNT(*) as cnt,
                          AVG(importance) as avg_importance
                   FROM memory_facts
                   WHERE user_id = ? AND valid_until IS NULL
                   GROUP BY fact_type""",
                (user_id,),
            ).fetchall()
            total = conn.execute(
                """SELECT COUNT(*) as total FROM memory_facts
                   WHERE user_id = ? AND valid_until IS NULL""",
                (user_id,),
            ).fetchone()
        by_type = {r["fact_type"]: {"count": r["cnt"], "avg_importance": r["avg_importance"]} for r in rows}
        return {"total": total["total"] if total else 0, "by_type": by_type}

    # ════════════════════════════════════════════════════════════
    # MEMORY RELATIONS
    # ════════════════════════════════════════════════════════════

    def add_relation(
        self,
        relation_id: str,
        user_id: str,
        source_entity: str,
        relation: str,
        target_entity: str,
        confidence: float = 1.0,
        source_fact: str | None = None,
        canonical_source: str | None = None,
        canonical_target: str | None = None,
    ) -> None:
        """Insert a relation, deduplicating on ``(user, source, rel, target)``
        across live rows.

        On UNIQUE collision (same triple is already valid), update
        confidence/source_fact/canonical fields rather than inserting a
        duplicate. Invalidated rows do not block re-insert.
        """
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO memory_relations
                       (relation_id, user_id, source_entity, relation,
                        target_entity, canonical_source, canonical_target,
                        confidence, source_fact)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, source_entity, relation, target_entity)
                       WHERE valid_until IS NULL
                       DO UPDATE SET
                           confidence = excluded.confidence,
                           source_fact = excluded.source_fact,
                           canonical_source = excluded.canonical_source,
                           canonical_target = excluded.canonical_target""",
                (relation_id, user_id, source_entity, relation, target_entity,
                 canonical_source, canonical_target, confidence, source_fact),
            )
            conn.commit()

    def get_relations(
        self,
        user_id: str,
        entity: str | None = None,
        canonical: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Get valid relations for a user.

        Parameters
        ----------
        entity : str | None
            Match against raw ``source_entity`` or ``target_entity``.
        canonical : str | None
            Match against ``canonical_source`` or ``canonical_target``.
            Use this when you've already resolved the entity.
        """
        with self._get_conn() as conn:
            if canonical:
                rows = conn.execute(
                    """SELECT * FROM memory_relations
                       WHERE user_id = ? AND valid_until IS NULL
                         AND (canonical_source = ? OR canonical_target = ?)
                       ORDER BY created_at DESC
                       LIMIT ?""",
                    (user_id, canonical, canonical, limit),
                ).fetchall()
            elif entity:
                rows = conn.execute(
                    """SELECT * FROM memory_relations
                       WHERE user_id = ? AND valid_until IS NULL
                         AND (source_entity = ? OR target_entity = ?)
                       ORDER BY created_at DESC
                       LIMIT ?""",
                    (user_id, entity, entity, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM memory_relations
                       WHERE user_id = ? AND valid_until IS NULL
                       ORDER BY created_at DESC
                       LIMIT ?""",
                    (user_id, limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def invalidate_relation(self, relation_id: str) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """UPDATE memory_relations
                   SET valid_until = CURRENT_TIMESTAMP
                   WHERE relation_id = ?""",
                (relation_id,),
            )
            conn.commit()

    # ════════════════════════════════════════════════════════════
    # ENTITY ALIASES (Faz 22D)
    # ════════════════════════════════════════════════════════════

    def get_alias(self, user_id: str, surface_form: str) -> str | None:
        """Return canonical_form for a surface form, or None if not aliased."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT canonical_form FROM memory_entity_aliases WHERE user_id = ? AND surface_form = ?",
                (user_id, surface_form),
            ).fetchone()
        return row["canonical_form"] if row else None

    def get_aliases_for_canonical(self, user_id: str, canonical: str) -> list[str]:
        """All surface forms that resolve to ``canonical``."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT surface_form FROM memory_entity_aliases WHERE user_id = ? AND canonical_form = ?",
                (user_id, canonical),
            ).fetchall()
        return [r["surface_form"] for r in rows]

    def set_alias(
        self, user_id: str, surface_form: str, canonical_form: str, source: str = "auto"
    ) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO memory_entity_aliases
                       (user_id, surface_form, canonical_form, source)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(user_id, surface_form) DO UPDATE SET
                       canonical_form = excluded.canonical_form,
                       source = excluded.source""",
                (user_id, surface_form, canonical_form, source),
            )
            conn.commit()

    def list_aliases(self, user_id: str) -> list[dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_entity_aliases WHERE user_id = ? ORDER BY canonical_form, surface_form",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ════════════════════════════════════════════════════════════
    # ENTITY PAGES (Faz 22D — LLM-compiled markdown summaries)
    # ════════════════════════════════════════════════════════════

    def upsert_entity_page(
        self,
        user_id: str,
        entity_canonical: str,
        content_md: str,
        source_fact_ids: list[str] | None = None,
        source_relation_ids: list[str] | None = None,
        surface_forms: list[str] | None = None,
    ) -> str:
        """Insert or update an entity page. Bumps version on update,
        clears stale flag, refreshes last_compiled_at.

        Returns the page_id.
        """
        import uuid

        existing = self.get_entity_page(user_id, entity_canonical)
        with self._get_conn() as conn:
            if existing:
                conn.execute(
                    """UPDATE memory_entity_pages
                           SET content_md = ?,
                               source_fact_ids = ?,
                               source_relation_ids = ?,
                               entity_surface_forms = ?,
                               fact_count = ?,
                               relation_count = ?,
                               version = version + 1,
                               stale = 0,
                               last_compiled_at = CURRENT_TIMESTAMP
                           WHERE page_id = ?""",
                    (
                        content_md,
                        json.dumps(source_fact_ids or []),
                        json.dumps(source_relation_ids or []),
                        json.dumps(surface_forms or []),
                        len(source_fact_ids or []),
                        len(source_relation_ids or []),
                        existing["page_id"],
                    ),
                )
                conn.commit()
                return existing["page_id"]
            page_id = str(uuid.uuid4())[:12]
            conn.execute(
                """INSERT INTO memory_entity_pages
                       (page_id, user_id, entity_canonical, entity_surface_forms,
                        content_md, source_fact_ids, source_relation_ids,
                        fact_count, relation_count, version, stale)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0)""",
                (
                    page_id, user_id, entity_canonical,
                    json.dumps(surface_forms or []),
                    content_md,
                    json.dumps(source_fact_ids or []),
                    json.dumps(source_relation_ids or []),
                    len(source_fact_ids or []),
                    len(source_relation_ids or []),
                ),
            )
            conn.commit()
            return page_id

    def get_entity_page(
        self, user_id: str, entity_canonical: str
    ) -> dict[str, Any] | None:
        """Fetch a compiled page by canonical entity. Bumps access stats."""
        with self._get_conn() as conn:
            row = conn.execute(
                """SELECT * FROM memory_entity_pages
                       WHERE user_id = ? AND entity_canonical = ?""",
                (user_id, entity_canonical),
            ).fetchone()
            if row:
                conn.execute(
                    """UPDATE memory_entity_pages
                           SET access_count = access_count + 1,
                               last_accessed_at = CURRENT_TIMESTAMP
                           WHERE page_id = ?""",
                    (row["page_id"],),
                )
                conn.commit()
        return dict(row) if row else None

    def list_entity_pages(
        self,
        user_id: str,
        only_fresh: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List entity pages for a user, ordered by access_count desc."""
        with self._get_conn() as conn:
            sql = """SELECT * FROM memory_entity_pages
                     WHERE user_id = ?"""
            params: list[Any] = [user_id]
            if only_fresh:
                sql += " AND stale = 0"
            sql += " ORDER BY access_count DESC, last_compiled_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def mark_entity_pages_stale(
        self, user_id: str, entity_canonical: str | None = None
    ) -> int:
        """Mark page(s) stale so the compiler picks them up next cycle.

        If ``entity_canonical`` is given, only that page; otherwise all.
        Returns the number of rows updated.
        """
        with self._get_conn() as conn:
            if entity_canonical:
                cursor = conn.execute(
                    """UPDATE memory_entity_pages SET stale = 1
                           WHERE user_id = ? AND entity_canonical = ?""",
                    (user_id, entity_canonical),
                )
            else:
                cursor = conn.execute(
                    "UPDATE memory_entity_pages SET stale = 1 WHERE user_id = ?",
                    (user_id,),
                )
            conn.commit()
            return cursor.rowcount

    def mark_pages_stale_by_fact(self, user_id: str, fact_id: str) -> int:
        """When a fact is invalidated, mark all pages whose
        ``source_fact_ids`` contain that id as stale. Used by the
        invalidate hook (Faz 22D Step 7).
        """
        with self._get_conn() as conn:
            # source_fact_ids is a JSON array — use LIKE on the serialized form.
            pattern = f'%"{fact_id}"%'
            cursor = conn.execute(
                """UPDATE memory_entity_pages
                       SET stale = 1
                       WHERE user_id = ? AND source_fact_ids LIKE ?""",
                (user_id, pattern),
            )
            conn.commit()
            return cursor.rowcount

    def forget_entity(
        self, user_id: str, canonical: str
    ) -> dict[str, int]:
        """Cascade-archive everything tied to an entity (Faz 22D Step 13).

        - Invalidate every relation where the entity appears as source or
          target (canonical or raw).
        - Invalidate every fact whose content mentions any known surface
          form for the canonical (cheap substring scan).
        - Hard-delete the entity page.

        Returns counts of {relations, facts, pages} archived. Audit-safe:
        nothing is hard-deleted from facts/relations — they live with
        ``valid_until`` set, so the supersede chain remains queryable.
        """
        if not canonical:
            return {"relations": 0, "facts": 0, "pages": 0}

        rels_n = 0
        facts_n = 0

        # 1. Invalidate matching relations (canonical OR raw match).
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT relation_id FROM memory_relations
                       WHERE user_id = ? AND valid_until IS NULL
                         AND (canonical_source = ? OR canonical_target = ?
                              OR source_entity = ? OR target_entity = ?)""",
                (user_id, canonical, canonical, canonical, canonical),
            ).fetchall()
            for r in rows:
                conn.execute(
                    """UPDATE memory_relations
                           SET valid_until = CURRENT_TIMESTAMP
                           WHERE relation_id = ?""",
                    (r["relation_id"],),
                )
                rels_n += 1
            conn.commit()

        # 2. Collect surface forms (canonical + aliases) and invalidate
        #    matching facts. Two-phase so invalidate_fact's hooks fire.
        forms = {canonical}
        forms.update(self.get_aliases_for_canonical(user_id, canonical))
        forms_lower = {f.lower() for f in forms if f}

        with self._get_conn() as conn:
            facts = conn.execute(
                """SELECT fact_id, content FROM memory_facts
                       WHERE user_id = ? AND valid_until IS NULL""",
                (user_id,),
            ).fetchall()
        target_ids: list[str] = []
        for f in facts:
            content = (f["content"] or "").lower()
            if any(form in content for form in forms_lower):
                target_ids.append(f["fact_id"])

        for fid in target_ids:
            self.invalidate_fact(fid)
            facts_n += 1

        # 3. Hard-delete the entity page (provenance lives in the
        #    invalidated facts; the page is just a derived view).
        page_deleted = 1 if self.delete_entity_page(user_id, canonical) else 0

        logger.info(
            f"forget_entity({user_id}, {canonical}): "
            f"{rels_n} relations, {facts_n} facts, {page_deleted} page(s) archived"
        )
        return {"relations": rels_n, "facts": facts_n, "pages": page_deleted}

    def delete_entity_page(self, user_id: str, entity_canonical: str) -> bool:
        """Hard-delete an entity page. Used for entity-level forget."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                """DELETE FROM memory_entity_pages
                       WHERE user_id = ? AND entity_canonical = ?""",
                (user_id, entity_canonical),
            )
            conn.commit()
            return cursor.rowcount > 0

    # ════════════════════════════════════════════════════════════
    # MEMORY PROCESSING LOG
    # ════════════════════════════════════════════════════════════

    def log_memory_processing(
        self,
        user_id: str,
        session_id: str | None = None,
        trigger: str = "session_close",
        facts_extracted: int = 0,
        facts_added: int = 0,
        facts_updated: int = 0,
        facts_invalidated: int = 0,
        duration_ms: int = 0,
    ) -> int:
        with self._get_conn() as conn:
            cursor = conn.execute(
                """INSERT INTO memory_processing_log
                   (user_id, session_id, trigger, facts_extracted,
                    facts_added, facts_updated, facts_invalidated, duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, session_id, trigger, facts_extracted,
                 facts_added, facts_updated, facts_invalidated, duration_ms),
            )
            conn.commit()
            return cursor.lastrowid or 0

    def get_processing_log(
        self, user_id: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        with self._get_conn() as conn:
            if user_id:
                rows = conn.execute(
                    """SELECT * FROM memory_processing_log
                       WHERE user_id = ?
                       ORDER BY processed_at DESC LIMIT ?""",
                    (user_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM memory_processing_log
                       ORDER BY processed_at DESC LIMIT ?""",
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

-- 6. User notes (unified: notes + preferences + favorites)
CREATE TABLE IF NOT EXISTS user_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    note_type TEXT DEFAULT 'note',
    content TEXT NOT NULL,
    key TEXT,
    metadata TEXT,
    source TEXT DEFAULT 'conversation',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
CREATE INDEX IF NOT EXISTS idx_notes_user ON user_notes(user_id, note_type);
CREATE UNIQUE INDEX IF NOT EXISTS idx_notes_pref_key ON user_notes(user_id, key)
    WHERE note_type = 'preference';

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
