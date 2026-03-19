# GBot Database Guide

## Overview

GBot uses **SQLite with WAL mode** as its single source of truth for all persistent data. The database is located at `/app/data/gbot.db` inside the Docker container (mapped to Docker volume `gbot_data`).

**Key Principles:**
- **SQLite is the source of truth** — LangGraph checkpointing is NOT used
- **14 tables** organized into 5 functional groups
- **Request-scoped operations** — data flows through FastAPI → GraphRunner → MemoryStore → SQLite
- **Foreign keys enforced** — maintains referential integrity
- **Indexes on frequent queries** — optimized for user/session lookups

---

## Database Structure (14 Tables)

### 📊 Table Groups

| Group | Tables | Purpose |
|-------|--------|---------|
| **Identity & Auth** | `users`, `user_channels`, `api_keys` | User accounts, cross-channel links, API access |
| **Conversations** | `sessions`, `messages` | Chat history and lifecycle |
| **Memory & Context** | `agent_memory`, `user_notes`, `favorites`, `preferences` | Long-term learning and personalization |
| **Scheduling** | `cron_jobs`, `cron_execution_log`, `reminders` | Periodic tasks and one-shot notifications |
| **Background Tasks** | `system_events`, `background_tasks` | Async subagent work and event delivery |

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

### 9. `cron_jobs` — Scheduled Tasks

**Purpose:** Periodic tasks with LLM processing (daily reports, monitoring, recurring queries).

**Schema:**
```sql
CREATE TABLE cron_jobs (
    job_id TEXT PRIMARY KEY,          -- UUID
    user_id TEXT NOT NULL,
    cron_expr TEXT NOT NULL DEFAULT '',  -- '0 9 * * *' = every day at 9am
    message TEXT NOT NULL,            -- Task description / prompt
    channel TEXT DEFAULT 'api',       -- Where to send results
    enabled INTEGER DEFAULT 1,        -- 0 = paused
    run_at TEXT,                      -- Next execution time
    agent_prompt TEXT,                -- Optional custom system prompt
    agent_tools TEXT,                 -- JSON list of allowed tools
    agent_model TEXT,                 -- Override default model
    notify_condition TEXT DEFAULT 'always',  -- 'always', 'notify_skip'
    consecutive_failures INTEGER DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
```

**Data Source:**
- **Agent tool:** `add_cron_job()`, `create_alert()` — LLM creates jobs
- **CLI:** `gbot cron add` (planned)

**Example Flow:**
```
User: "Every morning at 9am, check my GitHub stars and notify if there are new ones"

Agent calls:
add_cron_job(
    user_id="owner",
    cron_expr="0 9 * * *",
    message="Check GitHub stars, report if new",
    channel="telegram",
    agent_prompt="You are a monitoring agent...",
    notify_condition="notify_skip"  # Only send if there are new stars
)

# Stored in database
INSERT INTO cron_jobs (job_id, user_id, cron_expr, message, ...)
VALUES ('job-abc', 'owner', '0 9 * * *', 'Check GitHub stars...', ...);
```

**Execution:**
- **Background worker** (`CronScheduler`) checks jobs every minute
- Runs task via `LightAgent` (isolated, cheap execution)
- Results saved to `cron_execution_log`
- Sends notification via configured channel

**notify_skip Logic:**
```python
# Monitoring alerts only notify when needed
if notify_condition == "notify_skip" and response.strip().upper() == "[SKIP]":
    # Don't send notification, just log
    pass
else:
    # Send notification
    await send_message(channel, user_id, response)
```

---

### 10. `cron_execution_log` — Job Execution History

**Purpose:** Audit trail for cron job runs.

**Schema:**
```sql
CREATE TABLE cron_execution_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    result TEXT,                      -- LLM response
    status TEXT DEFAULT 'success',    -- 'success', 'error', 'skipped'
    tokens_used INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0
);
```

**Data Source:**
- **Auto-created** after each cron job execution by `CronScheduler`

**Example:**
```sql
INSERT INTO cron_execution_log (job_id, result, status, tokens_used, duration_ms)
VALUES ('job-abc', 'No new GitHub stars.', 'success', 150, 1200);
```

---

### 11. `reminders` — One-Shot & Recurring Notifications

**Purpose:** Simple message delivery without LLM processing (cheaper than cron_jobs).

**Schema:**
```sql
CREATE TABLE reminders (
    reminder_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    message TEXT NOT NULL,            -- Exact message to send
    channel TEXT DEFAULT 'telegram',
    run_at TEXT NOT NULL,             -- ISO timestamp for one-shot
    cron_expr TEXT,                   -- If set, recurring reminder
    status TEXT DEFAULT 'pending',    -- 'pending', 'sent', 'cancelled', 'failed'
    retry_count INTEGER DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sent_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
```

**Data Source:**
- **Agent tools:** `create_reminder()`, `create_recurring_reminder()`

**Example Flow:**
```
User (Telegram): "2 saat sonra hatırlat toplantı var"

Agent calls:
create_reminder(user_id="zynp", delay_seconds=7200, message="Toplantı var!", channel="telegram")

# Database insert
INSERT INTO reminders (reminder_id, user_id, message, channel, run_at, status)
VALUES ('rem-xyz', 'zynp', 'Toplantı var!', 'telegram', '2026-02-16T14:00:00Z', 'pending');
```

**Execution:**
- **Background worker** checks `reminders` table every minute
- Sends message at scheduled time (no LLM involved)
- Updates `status='sent'` and `sent_at`
- For recurring: schedules next run based on `cron_expr`

**Difference from cron_jobs:**
| Feature | `reminders` | `cron_jobs` |
|---------|-------------|-------------|
| LLM processing | ❌ No | ✅ Yes |
| Cost | Free (just message send) | Tokens charged |
| Use case | "Remind me to..." | "Daily summary report" |
| Message | Static text | Dynamic LLM response |

---

### 12. `system_events` — Delivery Queue for API/WS Channels

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

### 13. `background_tasks` — Subagent Results

**Purpose:** Stores background task metadata and results (delegated work via LightAgent).

**Schema:**
```sql
CREATE TABLE background_tasks (
    task_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    parent_session TEXT,              -- Session that spawned the task
    fallback_channel TEXT,            -- Where to send result if WS unavailable
    task_description TEXT NOT NULL,
    status TEXT DEFAULT 'running',    -- 'running', 'completed', 'failed'
    result TEXT,                      -- LLM response
    error TEXT,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
```

**Data Source:**
- **Agent tool:** `delegate()` (spawns background task)
- **SubagentWorker:** Updates status on completion/failure

**Example:**
```sql
-- Task created
INSERT INTO background_tasks (task_id, user_id, task_description, status)
VALUES ('task-123', 'owner', 'Research GBot competitors', 'running');

-- Task completed
UPDATE background_tasks
SET status='completed', result='Found 5 competitors: ...', completed_at=CURRENT_TIMESTAMP
WHERE task_id='task-123';
```

**Query:**
```python
# Check task status
task = db.get_background_task("task-123")
print(task["status"], task["result"])
```

---

### 14. `api_keys` — API Access Tokens

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

### 📅 Cron Job Flow

```
┌─────────────────────────────────────────────┐
│ Agent creates cron job                      │
│ add_cron_job(cron_expr="0 9 * * *", ...)   │
└──────┬──────────────────────────────────────┘
       │
       v
┌─────────────────────────────────────────────┐
│ Database Insert                             │
│ INSERT INTO cron_jobs (job_id, ...)         │
└──────┬──────────────────────────────────────┘
       │
       v
┌─────────────────────────────────────────────┐
│ Background Worker (CronScheduler)           │
│ - Checks every minute                       │
│ - Finds jobs where run_at <= now            │
│ - Executes via LightAgent                   │
└──────┬──────────────────────────────────────┘
       │
       v
┌─────────────────────────────────────────────┐
│ Execution Results                           │
│ - cron_execution_log: INSERT                │
│ - Send notification to channel              │
│ - Update job.run_at (next execution)        │
└─────────────────────────────────────────────┘
```

### ⏰ Reminder Flow

```
┌─────────────────────────────────────────────┐
│ User: "Remind me in 2 hours"                │
└──────┬──────────────────────────────────────┘
       │
       v
┌─────────────────────────────────────────────┐
│ Agent calls create_reminder()               │
│ (delay_seconds=7200)                        │
└──────┬──────────────────────────────────────┘
       │
       v
┌─────────────────────────────────────────────┐
│ Database Insert                             │
│ INSERT INTO reminders                       │
│ (run_at = now + 7200 seconds)               │
└──────┬──────────────────────────────────────┘
       │
       v
┌─────────────────────────────────────────────┐
│ Background Worker                           │
│ - Checks every minute                       │
│ - Finds reminders where run_at <= now       │
│ - Sends message (no LLM)                    │
│ - Updates status='sent'                     │
└─────────────────────────────────────────────┘
```

### 🔧 Background Task (Delegation) Flow

```
┌─────────────────────────────────────────────┐
│ User: "Research competitors in background"  │
└──────┬──────────────────────────────────────┘
       │
       v
┌─────────────────────────────────────────────┐
│ Agent calls delegate()                      │
└──────┬──────────────────────────────────────┘
       │
       v
┌─────────────────────────────────────────────┐
│ SubagentWorker.spawn()                      │
│ - INSERT INTO background_tasks              │
│ - Create asyncio task                       │
│ - Returns task_id immediately               │
└──────┬──────────────────────────────────────┘
       │
       v
┌─────────────────────────────────────────────┐
│ LightAgent executes (async)                 │
│ - Runs in background                        │
│ - No conversation history                   │
│ - Limited tool access                       │
└──────┬──────────────────────────────────────┘
       │
       v
┌─────────────────────────────────────────────┐
│ On Completion                               │
│ - UPDATE background_tasks (result, status)  │
│ - INSERT into session history               │
│ - Push via WebSocket (if connected)         │
│ - OR deliver via channel (Telegram/WA)      │
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

# Container starts → MemoryStore.__init__() → _SCHEMA executed
# All 15 tables created in /app/data/gbot.db
```

**Schema location:** `/root/gbot/gbot/memory/store.py` lines 932-1115

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

### Scheduled Tasks

```sql
-- All active reminders
SELECT * FROM reminders
WHERE status='pending'
ORDER BY run_at;

-- Cron jobs due for execution
SELECT * FROM cron_jobs
WHERE enabled=1
AND datetime(run_at) <= datetime('now');
```

### Background Tasks

```sql
-- Running tasks
SELECT * FROM background_tasks WHERE status='running';

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
SELECT 'reminders', COUNT(*) FROM reminders;
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
| `user_notes` | Agent tool `save_user_note()` | When LLM learns facts |
| `favorites` | Agent tool `add_favorite()` | User favorites management |
| `preferences` | Agent learning, planned API | User settings storage |
| `cron_jobs` | Agent tool `add_cron_job()` | Scheduled task creation |
| `cron_execution_log` | Auto-created by CronScheduler | After each cron run |
| `reminders` | Agent tool `create_reminder()` | Reminder requests |
| `system_events` | Background tasks, cron jobs | Task completion events |
| `background_tasks` | Agent tool `delegate()` | Background work delegation |

---

## Tools That Write to Database

| Tool Name | Tables Modified | Example Usage |
|-----------|----------------|---------------|
| `save_user_note` | `user_notes` | "Remember I prefer dark theme" |
| `add_favorite` | `favorites` | "Add this to favorites" |
| `create_reminder` | `reminders` | "Remind me in 2 hours" |
| `create_recurring_reminder` | `reminders` | "Every morning at 9am..." |
| `add_cron_job` | `cron_jobs` | "Daily check GitHub stars" |
| `create_alert` | `cron_jobs` | "Alert if price > $2000" |
| `delegate` | `background_tasks`, `messages` | "Research in background" |

---

## Conclusion

GBot's database is the **single source of truth** for all state:
- ✅ **14 tables** covering identity, conversations, memory, scheduling, and background work
- ✅ **Request-scoped operations** — no long-running state in LangGraph
- ✅ **Tool-driven data** — LLM decides when to save notes, schedule tasks, etc.
- ✅ **Automated backups** — daily snapshots with 7-day retention
- ✅ **WAL mode** — concurrent reads + safe writes

For more details on architecture decisions, see [`mimari_kararlar.md`](mimari_kararlar.md).

For API usage, see [`README.md`](README.md).

---

**Last Updated:** 2026-02-16
**GBot Version:** v1.14.0
**Author:** GBot Team
