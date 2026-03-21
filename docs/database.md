# GBot Database Guide

## Overview

GBot uses **SQLite with WAL mode** as its single source of truth for all persistent data. The database is located at `/app/data/gbot.db` inside the Docker container (mapped to Docker volume `gbot_data`).

**Key Principles:**
- **SQLite is the source of truth** — LangGraph checkpointing is NOT used
- **15 tables** organized into 5 functional groups
- **Request-scoped operations** — data flows through FastAPI → GraphRunner → MemoryStore → SQLite
- **Foreign keys enforced** — maintains referential integrity
- **Indexes on frequent queries** — optimized for user/session lookups

---

## Database Structure (12 Tables)

### 📊 Table Groups

| Group | Tables | Purpose |
|-------|--------|---------|
| **Identity & Auth** | `users`, `user_channels`, `api_keys` | User accounts, cross-channel links, API access |
| **Conversations** | `sessions`, `messages` | Chat history and lifecycle |
| **Memory & Context** | `agent_memory`, `user_notes`, `favorites`, `preferences` | Long-term learning and personalization |
| **Scheduling & Tasks** | `background_tasks`, `task_executions` | Unified task scheduling (immediate/delayed/recurring/monitor) + audit log |
| **Events** | `system_events` | Async event delivery queue |

---

## Detailed Table Reference

### 1. `users` — User Accounts

**Purpose:** Core user identity and authentication.

**Schema:**
```sql
CREATE TABLE users (
    user_id TEXT PRIMARY KEY,        -- Username (e.g., "owner", "zynp")
    name TEXT,                        -- Display name
    password_hash TEXT,               -- Bcrypt hash (if auth enabled)
    role TEXT DEFAULT 'user',         -- 'owner' or 'user'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Data Source:**
- **CLI:** `gbot user add <username>` → creates user with password
- **API:** `POST /auth/register` (if registration enabled)
- **Admin:** Direct database insert during setup

**When Created:**
- Server initialization creates the `owner` user (from `config.yaml`)
- Manually via CLI or admin API

**Example Flow:**
```bash
# CLI creates user
gbot user add zynp --password zeynep2026

# Stores in database
INSERT INTO users (user_id, name, password_hash, role)
VALUES ('zynp', 'Zeynep', '$2b$12$...', 'user');
```

---

### 2. `user_channels` — Cross-Channel Identity

**Purpose:** Links GBot users to external platform identities (Telegram, Discord, etc.).

**Schema:**
```sql
CREATE TABLE user_channels (
    user_id TEXT NOT NULL,            -- GBot user_id
    channel TEXT NOT NULL,            -- Platform: 'telegram', 'discord', 'api'
    channel_user_id TEXT NOT NULL,    -- Platform-specific ID (Telegram bot token, Discord ID)
    metadata TEXT DEFAULT '{}',       -- JSON: {"chat_id": 123456} for Telegram
    PRIMARY KEY (channel, channel_user_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
```

**Data Source:**
- **CLI:** `gbot user link <username> telegram <bot_token>`
- **API:** `POST /auth/link-channel`
- **Telegram webhook:** First message auto-saves `chat_id` to metadata

**When Created:**
- During user setup when linking external platforms
- Telegram channel stores bot token as `channel_user_id`

**Example Flow:**
```bash
# Link Telegram bot to user
gbot user link zynp telegram YOUR_TELEGRAM_BOT_TOKEN

# Database insert
INSERT INTO user_channels (user_id, channel, channel_user_id, metadata)
VALUES ('zynp', 'telegram', '8515420556:AAF...', '{}');

# When user sends first message, webhook updates metadata
UPDATE user_channels
SET metadata = '{"chat_id": 987654321}'
WHERE user_id = 'zynp' AND channel = 'telegram';
```

**Usage in Code:**
- `telegram_webhook()` → `db.get_channel_link(user_id, "telegram")` → retrieves bot token
- `send_message()` → uses `chat_id` from metadata to send Telegram messages

---

### 3. `sessions` — Chat Sessions

**Purpose:** Groups related messages into conversations with token tracking and lifecycle management.

**Schema:**
```sql
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,      -- UUID
    user_id TEXT NOT NULL,
    channel TEXT DEFAULT 'api',       -- 'api', 'telegram', etc.
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP,               -- NULL = active session
    summary TEXT,                     -- LLM-generated summary when closed
    token_count INTEGER DEFAULT 0,    -- Running total for rotation
    close_reason TEXT,                -- 'manual', 'token_limit', 'timeout'
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
CREATE INDEX idx_sessions_user ON sessions(user_id, started_at DESC);
```

**Data Source:**
- **Auto-created** on first message if no active session exists
- **API:** `POST /chat` (session_id optional in request)
- **Telegram:** Webhook finds or creates session per user

**When Created:**
- `GraphRunner.process()` → checks for active session → creates if missing
- Client can provide `session_id` to continue existing conversation

**Lifecycle:**
1. **Created:** First message without session_id
2. **Active:** `ended_at IS NULL`, messages accumulate, tokens tracked
3. **Closed:** Reaches token limit (30k default) → summary generated → new session started
4. **Ended:** Manual close via `POST /session/{id}/end`

**Example Flow:**
```python
# User sends first message via API
POST /chat {"message": "Hello"}

# GraphRunner creates session
session_id = db.create_session(user_id="owner", channel="api")

# Returns session_id to client
{"response": "Hi! How can I help?", "session_id": "abc-123"}

# Client includes session_id in next request
POST /chat {"message": "What's the weather?", "session_id": "abc-123"}

# Token limit check after each message
if token_count >= 30000:
    db.end_session(session_id, summary="User asked about weather...", close_reason="token_limit")
    new_session_id = db.create_session(user_id, channel)
```

**Query Examples:**
```sql
-- Get active sessions
SELECT * FROM sessions WHERE ended_at IS NULL;

-- Get user's recent sessions
SELECT * FROM sessions
WHERE user_id = 'owner'
ORDER BY started_at DESC
LIMIT 10;
```

---

### 4. `messages` — Chat History

**Purpose:** Stores all messages exchanged in conversations (user, assistant, tool).

**Schema:**
```sql
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,               -- 'user', 'assistant', 'tool'
    content TEXT,                     -- Message text
    tool_calls TEXT,                  -- JSON array for assistant tool calls
    tool_call_id TEXT,                -- Reference for tool responses
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
CREATE INDEX idx_messages_session ON messages(session_id, created_at);
```

**Data Source:**
- **Every chat interaction** → `GraphRunner.process()` → saves messages after LangGraph execution
- **API:** `POST /chat` → user message + assistant response + tool calls
- **Telegram:** Webhook → stores message pairs

**Message Types:**
| Role | Content Example | Tool Metadata |
|------|----------------|---------------|
| `user` | "Create a reminder for tomorrow" | - |
| `assistant` | "" (empty when calling tool) | `tool_calls=[{"name": "create_reminder", "args": {...}}]` |
| `tool` | "Reminder created: reminder_abc" | `tool_call_id="call_xyz"` |
| `assistant` | "I've set a reminder for tomorrow!" | - |

**Example Flow:**
```python
# User message via Telegram
await runner.process(user_id="zynp", channel="telegram", message="Remind me in 2 hours")

# Saved to database:
1. db.add_message(session_id, "user", "Remind me in 2 hours")

# LangGraph execution → agent calls create_reminder tool
2. db.add_message(session_id, "assistant", "", tool_calls='[{"name": "create_reminder", ...}]')
3. db.add_message(session_id, "tool", "Reminder set: ... in 120 minutes", tool_call_id="call_123")

# Final response
4. db.add_message(session_id, "assistant", "I've set a reminder for 2 hours from now!")
```

**Loading History:**
```python
# GraphRunner loads messages on each request
rows = db.get_session_messages(session_id)
messages = [
    HumanMessage(content=row["content"]) if row["role"] == "user"
    else AIMessage(content=row["content"], tool_calls=json.loads(row["tool_calls"]))
    # ... convert to LangChain messages
]
# Pass to LangGraph: graph.ainvoke({"messages": history + [new_message]})
```

---

### 5. `agent_memory` — Agent Knowledge Base

**Purpose:** Structured memory for facts the agent should always remember (replaces nanobot's MEMORY.md).

**Schema:**
```sql
CREATE TABLE agent_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT '',  -- '' = global, else user-specific
    key TEXT NOT NULL,                 -- Topic: 'project_info', 'user_preferences'
    content TEXT NOT NULL,             -- Markdown or plain text
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, key)
);
```

**Data Source:**
- **Manual:** Direct database insert for system-wide knowledge
- **Admin API:** `POST /admin/memory` (planned)
- **Agent learning:** Could auto-save from conversations (Faz 2+ feature)

**Example Data:**
```sql
INSERT INTO agent_memory (user_id, key, content) VALUES
('', 'project_info', 'GBot is a LangGraph-based assistant framework.'),
('owner', 'preferences', 'Prefers Turkish communication, uses uv for package management.');
```

**Usage:**
- `ContextBuilder` loads agent_memory in `_load_agent_memory()` → included in system prompt
- Not actively used in current version (placeholder for future features)

---

### 6. `user_notes` — Learned Facts

**Purpose:** Agent saves facts learned during conversations for future reference.

**Schema:**
```sql
CREATE TABLE user_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    note TEXT NOT NULL,               -- "User likes coffee without sugar"
    source TEXT DEFAULT 'conversation', -- 'conversation', 'manual'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
CREATE INDEX idx_notes_user ON user_notes(user_id);
```

**Data Source:**
- **Agent tool:** `save_user_note()` — LLM decides when to save facts
- **Manual:** Admin can insert notes

**Example Flow:**
```
User: "I prefer dark theme"
Agent: [calls save_user_note(user_id="owner", note="Prefers dark theme")]
Tool: "Note saved: Prefers dark theme"
Agent: "Got it! I'll remember you prefer dark theme."
```

**How It's Used:**
```python
# ContextBuilder includes notes in user context
notes = db.get_user_notes(user_id)
context = "User notes:\n" + "\n".join([f"- {n['note']}" for n in notes])
# Injected into system prompt → LLM sees it on every request
```

---

### 7. `favorites` — User Favorites

**Purpose:** Save items the user marks as favorites.

**Schema:**
```sql
CREATE TABLE favorites (
    user_id TEXT NOT NULL,
    item_id TEXT NOT NULL,            -- External ID or internal reference
    item_title TEXT NOT NULL,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, item_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
```

**Data Source:**
- **Agent tools:** `add_favorite()`, `remove_favorite()`, `get_favorites()`

**Example:**
```
User: "Add this to my favorites"
Agent: [calls add_favorite(user_id="owner", item_id="doc123", item_title="GBot Guide")]
```

---

### 8. `preferences` — User Settings

**Purpose:** Flexible JSON storage for user preferences (theme, language, notification settings, etc.).

**Schema:**
```sql
CREATE TABLE preferences (
    user_id TEXT PRIMARY KEY,
    data TEXT NOT NULL DEFAULT '{}',  -- JSON object
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
```

**Data Source:**
- **Agent learning:** Inferred from conversations
- **API:** `POST /user/{user_id}/preferences` (planned)

**Example Data:**
```json
{
  "theme": "dark",
  "language": "tr",
  "notifications": {
    "telegram": true,
    "email": false
  },
  "timezone": "Europe/Istanbul"
}
```

---

### 9. `background_tasks` — Unified Task Table

**Purpose:** Single table for ALL scheduled/background work: recurring jobs, delayed reminders, immediate subagent tasks, and monitoring alerts. Replaces old `cron_jobs`, `reminders`, and `background_tasks` tables (unified in v1.15.0).

**Schema:**
```sql
CREATE TABLE background_tasks (
    task_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    execution_type TEXT NOT NULL DEFAULT 'immediate',
        -- immediate | delayed | recurring | monitor
    processor TEXT NOT NULL DEFAULT 'agent',
        -- static | function | agent | runner
    message TEXT NOT NULL,
    channel TEXT DEFAULT 'api',
    cron_expr TEXT,                    -- recurring/monitor
    run_at TEXT,                       -- delayed one-shot
    enabled INTEGER DEFAULT 1,        -- recurring: pause/resume
    agent_prompt TEXT,
    agent_tools TEXT,                  -- JSON list
    agent_model TEXT,
    notify_condition TEXT DEFAULT 'always',
    plan_json TEXT,
    status TEXT DEFAULT 'pending',
        -- pending | running | completed | failed | cancelled | paused
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
```

**execution_type × processor Matrix:**

| execution_type | processor | Use Case |
|---------------|-----------|----------|
| `immediate` | `agent` | Background research, delegation |
| `delayed` | `static` | Simple reminder ("2 saat sonra hatırlat") |
| `delayed` | `agent` | Scheduled LLM task |
| `recurring` | `static` | Periodic notification (no LLM) |
| `recurring` | `agent` | Daily summary report (LLM) |
| `monitor` | `agent` | Price alert with NOTIFY/SKIP logic |

**Data Source:**
- **Agent tool:** `delegate()` — DelegationPlanner creates tasks
- **CronScheduler:** Registers with APScheduler on startup

**Example Flows:**
```python
# Recurring monitor
db.create_task("task-abc", "owner", "Check gold price",
    execution_type="monitor", processor="agent",
    cron_expr="0 9 * * *", channel="telegram",
    agent_prompt="You are a price monitor...",
    notify_condition="notify_skip")

# Simple delayed reminder
db.create_task("task-xyz", "owner", "Toplantı var!",
    execution_type="delayed", processor="static",
    run_at="2026-03-19T14:00:00", channel="telegram")

# Immediate background task
db.create_task("task-123", "owner", "Research competitors",
    execution_type="immediate", processor="agent",
    channel="api")
```

---

### 10. `task_executions` — Task Execution History

**Purpose:** Unified audit trail for all task executions. Replaces old `cron_execution_log` and `delegation_log` tables.

**Schema:**
```sql
CREATE TABLE task_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    execution_type TEXT,
    processor_type TEXT,
    status TEXT DEFAULT 'success',    -- success | error | skipped | planned
    result TEXT,
    error TEXT,
    tokens_used INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    plan_json TEXT,
    reference_id TEXT,
    executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Data Source:**
- **CronScheduler:** Logs after each task execution
- **DelegationPlanner:** Logs planned tasks (status='planned')

---

### 11. `system_events` — Delivery Queue for API/WS Channels

**Purpose:** Message queue for API/WS channels when no active connection exists. Used for polling and WebSocket push.

**Schema:**
```sql
CREATE TABLE system_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    channel TEXT,                     -- 'api', 'ws' (channel the event targets)
    session_id TEXT,                  -- Session context (if available)
    source TEXT,                      -- 'cron', 'task:abc'
    event_type TEXT,                  -- 'message', 'task_completed'
    payload TEXT,                     -- Event data (JSON or text)
    is_delivered BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
```

**Data Source:**
- **Background tasks:** `SubagentWorker` creates events when tasks complete
- **Cron jobs:** Scheduler creates events for alerts

**Example Flow:**
```python
# User delegates a task
User: "Search for GBot competitors and summarize"

# Agent calls delegate tool → spawns background task
task_id = subagent_worker.spawn(
    user_id="owner",
    task="Search for GBot competitors and summarize",
    tools=["web_search", "web_fetch"]
)

# Background task executes
result = await light_agent.run("Search for GBot competitors...")

# On completion, create system event
db.add_system_event(
    user_id="owner",
    source=f"task:{task_id}",
    event_type="task_completed",
    payload=result[:500]
)

# WebSocket pushes event to connected client
# OR client polls: GET /events/owner
```

**Delivery:**
- **WebSocket:** `ws_manager.send_event()` → marks `is_delivered=TRUE` if successful
- **Polling:** `GET /events/{user_id}` → returns undelivered events → marks delivered

---

### 12. `memory_facts` — Learned Knowledge (Auto-extracted)

**Purpose:** Long-term memory — typed facts extracted from conversations via LLM. Semantic search via `vec_memory_facts` (sqlite-vec).

**Schema:**
```sql
CREATE TABLE memory_facts (
    fact_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    content TEXT NOT NULL,
    fact_type TEXT NOT NULL DEFAULT 'semantic',  -- semantic | episodic | preference | procedural
    source TEXT DEFAULT 'extraction',            -- extraction | note_transfer
    source_session TEXT,
    source_channel TEXT,
    confidence REAL DEFAULT 1.0,
    importance REAL DEFAULT 0.5,
    access_count INTEGER DEFAULT 0,
    valid_from TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    valid_until TIMESTAMP,                       -- NULL = active, set = invalidated
    superseded_by TEXT,                          -- fact_id that replaced this one
    keywords TEXT,                               -- JSON array
    category TEXT,
    embedding BLOB,
    created_at TIMESTAMP, updated_at TIMESTAMP
);

CREATE VIRTUAL TABLE vec_memory_facts USING vec0(
    embedding float[3072] distance_metric=cosine
);
```

**AUDN Update Logic:** New facts are embedded → similar facts found via sqlite-vec KNN → LLM decides:
- **ADD**: genuinely new information
- **UPDATE**: replaces existing fact (old invalidated, new added)
- **DELETE**: negates existing fact (old invalidated, no new fact)
- **NOOP**: duplicate, skip

---

### 13. `memory_processing_log` — Extraction Audit Trail

**Purpose:** Tracks when memory processing ran, what was extracted/updated.

**Schema:**
```sql
CREATE TABLE memory_processing_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    session_id TEXT,
    trigger TEXT,           -- hot_path | session_close | manual
    facts_extracted INTEGER, facts_added INTEGER,
    facts_updated INTEGER, facts_invalidated INTEGER,
    duration_ms INTEGER,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

### 14. `memory_relations` — Entity Relationships

**Purpose:** Tracks relationships between entities extracted from conversations.

**Schema:**
```sql
CREATE TABLE memory_relations (
    relation_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    source_entity TEXT NOT NULL,    -- "Ömer"
    relation TEXT NOT NULL,         -- "works_at", "married_to", "owns"
    target_entity TEXT NOT NULL,    -- "HangiKredi", "Ayşe", "Pamuk"
    confidence REAL DEFAULT 1.0,
    valid_from TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    valid_until TIMESTAMP,
    source_fact TEXT,               -- fact_id that generated this relation
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Relation types:** works_at, works_with, lives_in, owns, married_to, knows, uses, studies

**Data Source:** Auto-extracted by MemoryService during fact extraction. Not yet used in ContextBuilder (planned for future).

---

### 15. `api_keys` — API Access Tokens

**Purpose:** Alternative authentication method (API keys instead of JWT).

**Schema:**
```sql
CREATE TABLE api_keys (
    key_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    key_hash TEXT NOT NULL,           -- Bcrypt hash of API key
    name TEXT,                        -- Label: "Production key", "Test key"
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,             -- NULL = never expires
    is_active BOOLEAN DEFAULT TRUE,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
```

**Data Source:**
- **CLI:** `gbot user create-api-key <username>`
- **API:** `POST /auth/api-keys` (owner only)

**Usage:**
```bash
# Create API key
gbot user create-api-key owner

# Returns: gbot_sk_abc123xyz (shown once, then hashed)

# Use in API requests
curl -H "Authorization: Bearer gbot_sk_abc123xyz" https://gbot-assistant.cloud/chat
```

**Planned feature** (not fully implemented in Faz 16).

---

## Data Flow Diagrams

### 🔄 Chat Message Flow

```
┌─────────────┐
│ User sends  │
│ "Hello"     │
└──────┬──────┘
       │
       v
┌─────────────────────────────────────────────┐
│ FastAPI Endpoint                            │
│ POST /chat or Telegram Webhook              │
└──────┬──────────────────────────────────────┘
       │
       v
┌─────────────────────────────────────────────┐
│ GraphRunner.process()                       │
│ 1. Find/create session                      │
│ 2. Load history from messages table         │
│ 3. Build context (notes, memory, prefs)     │
│ 4. Execute LangGraph                        │
│ 5. Save new messages                        │
│ 6. Check token limit                        │
└──────┬──────────────────────────────────────┘
       │
       v
┌─────────────────────────────────────────────┐
│ Database Writes                             │
│ - messages: user + assistant + tool         │
│ - sessions: update token_count              │
│ - user_notes: if agent called save_note()   │
│ - favorites: if agent added favorite        │
└─────────────────────────────────────────────┘
```

### 📅 Unified Task Flow (Recurring / Delayed / Immediate)

```
┌─────────────────────────────────────────────┐
│ Agent calls delegate()                      │
│ DelegationPlanner → execution_type +        │
│ processor + schedule                        │
└──────┬──────────────────────────────────────┘
       │
       v
┌─────────────────────────────────────────────┐
│ Database Insert                             │
│ INSERT INTO background_tasks                │
│ (task_id, execution_type, processor, ...)   │
└──────┬──────────────────────────────────────┘
       │
       ├── immediate → SubagentWorker.spawn()
       │                (async LightAgent)
       │
       ├── delayed   → APScheduler DateTrigger
       │                (fire once at run_at)
       │
       ├── recurring → APScheduler CronTrigger
       │                (fire on cron_expr)
       │
       └── monitor   → APScheduler CronTrigger
                        (NOTIFY/SKIP logic)
       │
       v
┌─────────────────────────────────────────────┐
│ Execution                                   │
│ - static: send message directly             │
│ - agent: LightAgent isolated run            │
│ - runner: full GraphRunner.process()        │
│ - function: call Python function            │
└──────┬──────────────────────────────────────┘
       │
       v
┌─────────────────────────────────────────────┐
│ Results                                     │
│ - task_executions: INSERT (audit log)       │
│ - background_tasks: UPDATE (status/result)  │
│ - Deliver via channel (WS/Telegram/WA)      │
│ - Inject into session history               │
└─────────────────────────────────────────────┘
```

---

## Database Initialization

### When Tables Are Created

**Container startup:**
```python
# gbot/memory/store.py
class MemoryStore:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._init_db()  # Executes CREATE TABLE IF NOT EXISTS for all 15 tables
```

**First run:**
```bash
docker compose up -d

# Container starts → MemoryStore.__init__() → _SCHEMA + _TASK_TABLES_SQL executed
# All 15 tables created in /app/data/gbot.db
```

**Auto-migration:** If upgrading from v1.14.x, `_migrate_to_unified_tasks()` automatically migrates old `cron_jobs`, `reminders`, `cron_execution_log`, `delegation_log` tables into the new unified schema in a single transaction.

---

## Backup & Maintenance

### Automated Backup

**Script:** `/root/backup-db.sh`
```bash
#!/bin/bash
BACKUP_DIR="/root/gbot-backups"
DB_PATH="/var/lib/docker/volumes/gbot_data/_data/gbot.db"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Copy + compress
cp "$DB_PATH" "$BACKUP_DIR/gbot_${TIMESTAMP}.db"
gzip "$BACKUP_DIR/gbot_${TIMESTAMP}.db"

# Keep last 7 days
find "$BACKUP_DIR" -name "gbot_*.db.gz" -mtime +7 -delete
```

**Cron schedule:** Daily at 02:00 AM
```bash
0 2 * * * /root/backup-db.sh
```

### Manual Backup

```bash
# From host
docker exec gbot sqlite3 /app/data/gbot.db ".backup /tmp/backup.db"
docker cp gbot:/tmp/backup.db ./gbot_backup_$(date +%Y%m%d).db
```

### Restore

```bash
# Stop container
docker compose down

# Replace database
cp gbot_backup_20260216.db /var/lib/docker/volumes/gbot_data/_data/gbot.db

# Restart
docker compose up -d
```

---

## Query Examples

### User Management

```sql
-- List all users
SELECT * FROM users;

-- Get user with channels
SELECT u.user_id, u.name, uc.channel, uc.channel_user_id
FROM users u
LEFT JOIN user_channels uc ON u.user_id = uc.user_id;
```

### Active Sessions

```sql
-- Active sessions per user
SELECT user_id, COUNT(*) as active_count
FROM sessions
WHERE ended_at IS NULL
GROUP BY user_id;

-- Session with message count
SELECT s.session_id, s.user_id, s.started_at, COUNT(m.id) as msg_count
FROM sessions s
LEFT JOIN messages m ON s.session_id = m.session_id
WHERE s.ended_at IS NULL
GROUP BY s.session_id;
```

### User Context

```sql
-- Full user profile
SELECT
    (SELECT COUNT(*) FROM user_notes WHERE user_id='owner') as notes_count,
    (SELECT COUNT(*) FROM favorites WHERE user_id='owner') as favorites_count,
    (SELECT data FROM preferences WHERE user_id='owner') as preferences;
```

### Tasks

```sql
-- All active recurring tasks
SELECT * FROM background_tasks
WHERE execution_type IN ('recurring', 'monitor')
AND enabled=1;

-- Pending delayed tasks (reminders)
SELECT * FROM background_tasks
WHERE execution_type='delayed'
AND status='pending'
ORDER BY run_at;

-- Running immediate tasks
SELECT * FROM background_tasks WHERE status='running';

-- Recent execution log
SELECT * FROM task_executions
ORDER BY executed_at DESC LIMIT 20;

-- Undelivered events
SELECT * FROM system_events
WHERE user_id='owner'
AND is_delivered=FALSE
ORDER BY created_at DESC;
```

---

## Performance Considerations

### Indexes

Current indexes optimize these queries:
- `idx_sessions_user` → User's recent sessions
- `idx_messages_session` → Session history retrieval
- `idx_notes_user` → User context building

### WAL Mode

```sql
PRAGMA journal_mode=WAL;
```

**Benefits:**
- Concurrent reads while writing
- Better performance for write-heavy workloads
- Safer crash recovery

**Files created:**
- `gbot.db` — main database
- `gbot.db-wal` — write-ahead log
- `gbot.db-shm` — shared memory

### Database Size Monitoring

```bash
# Check database size
docker exec gbot du -h /app/data/gbot.db

# Check table row counts
docker exec gbot sqlite3 /app/data/gbot.db "
SELECT 'users' as tbl, COUNT(*) FROM users UNION ALL
SELECT 'sessions', COUNT(*) FROM sessions UNION ALL
SELECT 'messages', COUNT(*) FROM messages UNION ALL
SELECT 'background_tasks', COUNT(*) FROM background_tasks;
"
```

---

## Troubleshooting

### Database Locked

**Cause:** Multiple processes accessing database simultaneously.

**Fix:**
```bash
# Check WAL checkpoint
docker exec gbot sqlite3 /app/data/gbot.db "PRAGMA wal_checkpoint(TRUNCATE);"
```

### Corrupted Database

**Check integrity:**
```bash
docker exec gbot sqlite3 /app/data/gbot.db "PRAGMA integrity_check;"
```

**Restore from backup if corrupted.**

### Missing Tables

**Re-run schema initialization:**
```bash
# Force recreation (WARNING: deletes data)
docker exec gbot rm /app/data/gbot.db
docker compose restart
```

---

## Summary: Table → Data Source Quick Reference

| Table | Primary Data Source | When Created |
|-------|---------------------|--------------|
| `users` | CLI `gbot user add`, API registration | Manual user creation |
| `user_channels` | CLI `gbot user link`, API `/auth/link-channel` | Channel linking |
| `api_keys` | CLI `gbot user create-api-key` | API key generation |
| `sessions` | Auto-created on first message | Every new conversation |
| `messages` | Every chat request (API, Telegram) | Every message exchange |
| `agent_memory` | Manual insert, planned admin API | System knowledge seeding |
| `user_notes` | Agent tools (save_note, set_preference, add_favorite) | Temporal buffer — processed by MemoryService |
| `memory_facts` | MemoryService (auto-extraction + AUDN) | Every N messages (hot-path) + session close |
| `memory_processing_log` | MemoryService | After each extraction run |
| `memory_relations` | MemoryService (auto-extraction) | Entity relationships from conversations |
| `background_tasks` | Agent tool `delegate()` | All task types (immediate/delayed/recurring/monitor) |
| `task_executions` | Auto-created by CronScheduler | After each task execution |
| `system_events` | Background tasks, scheduler | Event delivery queue |

---

## Tools That Write to Database

| Tool Name | Tables Modified | Example Usage |
|-----------|----------------|---------------|
| `save_user_note` | `user_notes` | "Remember I prefer dark theme" |
| `add_favorite` | `favorites` | "Add this to favorites" |
| `delegate` | `background_tasks`, `task_executions`, `messages` | All scheduling: reminders, recurring jobs, monitors, background research |
| `list_scheduled_tasks` | `background_tasks` (read) | "What tasks are active?" |
| `cancel_scheduled_task` | `background_tasks` | "Cancel that reminder" |

---

## Conclusion

GBot's database is the **single source of truth** for all state:
- ✅ **15 tables** covering identity, conversations, memory, scheduling, and background work
- ✅ **Request-scoped operations** — no long-running state in LangGraph
- ✅ **Tool-driven data** — LLM decides when to save notes, schedule tasks, etc.
- ✅ **Automated backups** — daily snapshots with 7-day retention
- ✅ **WAL mode** — concurrent reads + safe writes

For more details on architecture decisions, see [`mimari_kararlar.md`](mimari_kararlar.md).

For API usage, see [`README.md`](README.md).

---

**Last Updated:** 2026-03-19
**GBot Version:** v1.15.0
**Author:** GBot Team
