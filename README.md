# GraphBot

Extensible AI assistant framework built on LangGraph.

Multi-channel support, long-term memory, background tasks, tool system, and an interactive CLI — all backed by SQLite as the single source of truth.

## What is this project for?

GraphBot is designed to help you build a **production-ready personal/team assistant** that can move beyond plain chat:

- Persist conversation state and user memory in a simple local database (SQLite)
- Run tool-augmented workflows (files, shell, web, reminders, cron jobs, delegation)
- Serve users through API, CLI, WebSocket, and messaging channels from one core runtime
- Keep the agent loop stateless while maintaining durable operational history and tasks

In short: this project aims to be a practical assistant platform you can run, extend, and operate without heavyweight infrastructure.

## Quick Start

### 1. Install

```bash
uv sync --extra dev
```

### 2. Configure

Copy and edit the config file:

```yaml
# config.yaml
assistant:
  name: "GraphBot"
  owner:
    username: "ali"
    name: "Ali"
  model: "openai/gpt-4o-mini"

providers:
  openai:
    api_key: "sk-..."     # or set via .env
```

Or use environment variables (`.env`):
```
GRAPHBOT_PROVIDERS__OPENAI__API_KEY=sk-...
```

### 3. Run

```bash
gbot run                    # start API server on :8000
gbot                        # open interactive REPL
```

That's it. The REPL connects to the API server automatically.

---

## Architecture

![architecture](images/architecture.png)

**Core design decisions:**

| Principle | Description |
|-----------|-------------|
| **LangGraph = stateless** | No checkpoint — used purely as an execution engine |
| **SQLite = source of truth** | 15 tables for sessions, memory, users, tasks, events |
| **GraphRunner = orchestrator** | Request-scoped bridge between SQLite and LangGraph |
| **LiteLLM = multi-provider** | OpenAI, Anthropic, DeepSeek, Groq, Gemini, OpenRouter |

The agent graph has 4 nodes: `load_context` → `reason` ⇄ `execute_tools` → `respond`

---

## Features

| Feature | Description |
|---------|-------------|
| Multi-provider LLM | 6+ providers via LiteLLM |
| Multi-channel | Telegram (active), Discord/WhatsApp/Feishu (stub) |
| Long-term memory | Notes, preferences, favorites, activity logs in SQLite |
| Session management | Token-limit based with automatic LLM summary on transition |
| 9 tool groups | Memory, filesystem, shell, web search, RAG, cron, reminder, delegate |
| Skill system | Markdown-based, workspace override, always-on support |
| Background tasks | Cron scheduler, one-shot reminders, heartbeat, async subagents |
| Interactive CLI | `gbot` — Rich REPL with slash commands and autocomplete |
| Admin API | Server status, config, skills, users, cron management |
| RAG | Optional FAISS + sentence-transformers semantic search |
| WebSocket | Real-time chat support |
| Docker | Single-command deployment with docker-compose |

---

## Usage

### CLI Commands

```bash
gbot                                     # interactive REPL (default)
gbot run [--port 8000] [--reload]        # API server
gbot chat -m "hello"                     # single message via API
gbot chat --local -m "hello"             # local mode (no server needed)
gbot status                              # system info
gbot --version                           # version
```

User management:
```bash
gbot user add ali --name "Ali" --password "pass123" --telegram "BOT_TOKEN"
gbot user list
gbot user remove ali
gbot user set-password ali newpass
gbot user link ali telegram 12345
```

Credentials:
```bash
gbot login ali -s http://localhost:8000  # saves token to ~/.graphbot/
gbot logout
```

Cron jobs:
```bash
gbot cron list
gbot cron remove <job_id>
```

### REPL Slash Commands

Type `/` inside the REPL for autocomplete:

| Command | Description |
|---------|-------------|
| `/help` | Command list |
| `/status` | Server status |
| `/session info\|new\|list\|end` | Session management |
| `/history [n]` | Last n messages |
| `/context` | User context |
| `/model` | Active model |
| `/config` | Server configuration |
| `/skill` | Skill list |
| `/cron list\|remove <id>` | Cron management |
| `/user` | User list |
| `/events` | Pending events |
| `/clear` | Clear screen |
| `/exit` | Exit |

### REST API

| Method | Path | Description |
|--------|------|-------------|
| POST | `/chat` | Send message, get response |
| GET | `/health` | Health check |
| GET | `/sessions/{user_id}` | User's sessions |
| GET | `/session/{sid}/history` | Session message history |
| POST | `/session/{sid}/end` | End session |
| GET | `/user/{user_id}/context` | User context |
| POST | `/auth/register` | Register (owner-only) |
| POST | `/auth/login` | Login |
| WS | `/ws/chat` | WebSocket chat |
| POST | `/webhooks/telegram/{user_id}` | Telegram webhook |

### Admin Endpoints (owner-only)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/admin/status` | Server status |
| GET | `/admin/config` | Sanitized configuration |
| GET | `/admin/skills` | Skill list |
| GET | `/admin/users` | User list |
| GET | `/admin/crons` | Cron job list |
| DELETE | `/admin/crons/{job_id}` | Delete cron job |
| GET | `/admin/logs` | Activity logs |

### Tool Usage (via Chat)

Tools are invoked with natural language — the LLM picks the right one:

```
"Save this note: meeting tomorrow"                   → save_user_note
"Set a reminder in 5 minutes: take medicine"          → create_reminder
"Check the weather every morning at 9"                → add_cron_job
"Alert me if gold goes above $2000"                   → create_alert
"Do this in the background: research topic X"         → delegate
```

---

## Configuration

Priority order: `.env` > environment variables > `config.yaml` > defaults

```bash
# .env uses GRAPHBOT_ prefix with __ separator
GRAPHBOT_ASSISTANT__MODEL=openai/gpt-4o-mini
GRAPHBOT_PROVIDERS__OPENAI__API_KEY=sk-...
GRAPHBOT_BACKGROUND__CRON__ENABLED=true
```

Full config reference: [`config.yaml`](./config.yaml)

### Authentication

GraphBot runs in two modes:

#### Auth Disabled (default)

The `auth.jwt_secret_key` field in `config.yaml` controls authentication. By default it is empty (`""`), which means auth is disabled — all endpoints are open and `user_id` is passed in the request body:

```yaml
# config.yaml (default)
auth:
  jwt_secret_key: ""    # empty = auth disabled
```

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "hello", "user_id": "ali"}'
```

#### Auth Enabled

Set `jwt_secret_key` to a 32+ character secret to enable JWT authentication:

```yaml
# config.yaml
auth:
  jwt_secret_key: "your-secret-key-at-least-32-characters"
  access_token_expire_minutes: 1440   # 24 hours (default)
```

Or via `.env`:
```bash
GRAPHBOT_AUTH__JWT_SECRET_KEY=your-secret-key-at-least-32-characters
```

| State | `auth.jwt_secret_key` | Access |
|-------|----------------------|--------|
| Auth disabled | `""` (empty, default) | Open — `user_id` in request body |
| Auth enabled | `"your-secret..."` | JWT token or API key required |

#### User Management

Users are managed via the `gbot` CLI, which writes directly to the SQLite database. This is the **primary way** to create users — no running server or authentication needed:

```bash
# Create user with password
gbot user add ali --name "Ali" --password "pass123"

# Create user + link Telegram bot in one command
gbot user add ali --name "Ali" --password "pass123" --telegram "BOT_TOKEN"

# Change password
gbot user set-password ali newpass

# Link a channel to an existing user
gbot user link ali telegram 12345

# List all users
gbot user list

# Remove user
gbot user remove ali
```

> **Important:** When auth is enabled, users must exist in the database before they can login. The `owner` defined in `config.yaml` is auto-created at server startup, but all other users must be added via CLI first.

#### Login & Token Flow

Once a user exists, they can authenticate:

**CLI login** — saves token to `~/.graphbot/credentials.json`:

```bash
gbot login ali -s http://localhost:8000   # prompts for password
gbot                                       # uses saved token automatically
gbot logout                                # clears saved credentials
```

**API login:**

```bash
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"user_id": "ali", "password": "pass123"}'
# → {"success": true, "token": "eyJhbG...", "user_id": "ali"}
```

**Using the token:**

```bash
TOKEN="eyJhbG..."

curl -X POST http://localhost:8000/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "hello"}'
```

#### API Keys (Alternative)

For persistent access without token refresh, create an API key:

```bash
# Create (requires token auth)
curl -X POST http://localhost:8000/auth/api-keys \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-key"}'
# → {"key": "abc123...", "key_id": "..."}

# Use via header
curl -X POST http://localhost:8000/chat \
  -H "X-API-Key: abc123..." \
  -H "Content-Type: application/json" \
  -d '{"message": "hello"}'
```

#### Adding Users: CLI vs API

There are two ways to add users:

| Method | When to use | Auth needed? |
|--------|-------------|-------------|
| `gbot user add` (CLI) | Initial setup, server admin tasks | No — direct DB access |
| `POST /auth/register` (API) | Remote user creation by owner | Yes — owner token required |

```bash
# CLI — works anytime, no server needed
gbot user add veli --name "Veli" --password "pass456"

# API — only owner can register, requires running server + auth
curl -X POST http://localhost:8000/auth/register \
  -H "Authorization: Bearer $OWNER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "veli", "name": "Veli", "password": "pass456"}'
```

#### Rate Limiting

Default: 60 requests/minute. Configure in `config.yaml`:

```yaml
auth:
  rate_limit:
    enabled: true
    requests_per_minute: 120
```

### Tool System

9 tool groups, enabled via `tools: ["*"]` in config:

| Group | Tools | Description |
|-------|-------|-------------|
| Memory | save_user_note, get_user_context, log_activity, favorites | User memory |
| Filesystem | read_file, write_file, edit_file, list_dir | Workspace files |
| Shell | exec_command | Safe shell (destructive commands blocked) |
| Web | web_search, web_fetch | Brave Search + page fetch |
| RAG | search_items, get_item_detail | Semantic search (FAISS) |
| Cron | add/list/remove_cron_job, create_alert | Recurring tasks + NOTIFY/SKIP |
| Reminder | create/list/cancel_reminder | One-shot delayed messages |
| Delegate | delegate | Background subagent spawn |

### Skill System

Markdown-based skills in `workspace/skills/` (override built-in ones):

```markdown
---
name: weather
description: Query weather information
always: false
metadata:
  requires:
    bins: [curl]
---
# Weather Skill
...instructions...
```

- `always: true` → always included in system prompt
- Requirements check: skill disabled if binary/env var is missing

### Background Services

| Service | Description |
|---------|-------------|
| CronScheduler | APScheduler with cron expressions |
| LightAgent | Lightweight isolated agent for cron alerts (NOTIFY/SKIP) |
| Reminder | One-shot delayed messages (no LLM, direct delivery) |
| Heartbeat | Periodic wake-up, reads HEARTBEAT.md |
| SubagentWorker | Async background task spawn with DB persistence |

**Delegate flow:**
```
User: "Do this in the background: ..."
  → delegate tool → SubagentWorker → background_tasks table
  → On completion: system_event created
  → Next user message: ContextBuilder injects result into prompt
```

### RAG (Optional)

```bash
uv sync --extra rag   # FAISS + sentence-transformers
```

```yaml
rag:
  embedding_model: "intfloat/multilingual-e5-small"
  data_source: "./data/items.json"
  index_path: "./data/faiss_index"
```

When enabled, `search_items` and `get_item_detail` tools are automatically added.

### Telegram Bot

Each user creates their own Telegram bot; the token is stored in the `user_channels` table.

```bash
# 1. Create a bot via @BotFather and get the token
# 2. Enable telegram in config.yaml (channels.telegram.enabled: true)
# 3. Add user and link the bot token
gbot user add ali --name "Ali" --telegram "123456:ABC_TOKEN"
# 4. Create a public URL (e.g. ngrok http 8000)
# 5. Register webhook
curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://xxxx.ngrok-free.app/webhooks/telegram/ali"
# 6. Start the server
gbot run
```

---

## Docker

```bash
docker compose up -d         # start
docker compose logs -f       # follow logs
docker compose down          # stop
```

Uses named volumes (`graphbot_data`, `graphbot_workspace`) and `config.yaml` as read-only bind mount.

---

## Project Structure

```
graphbot/                          # Core framework
├── agent/
│   ├── context.py                 # ContextBuilder (6-layer system prompt)
│   ├── graph.py                   # StateGraph compile
│   ├── nodes.py                   # 4 nodes: load_context, reason, execute_tools, respond
│   ├── runner.py                  # GraphRunner orchestrator
│   ├── light.py                   # LightAgent (background tasks)
│   ├── state.py                   # AgentState(MessagesState)
│   ├── skills/                    # Skill loader + built-ins
│   └── tools/                     # 9 tool groups
├── api/
│   ├── app.py                     # FastAPI app + lifespan
│   ├── routes.py                  # Chat, health, sessions, user endpoints
│   ├── admin.py                   # Admin endpoints (owner-only)
│   ├── auth.py                    # JWT + API key auth
│   ├── ws.py                      # WebSocket chat
│   └── deps.py                    # Dependency injection
├── core/
│   ├── config/                    # YAML + BaseSettings + .env
│   ├── providers/                 # LiteLLM wrapper
│   ├── channels/                  # Telegram, base channel
│   ├── cron/                      # APScheduler + types
│   └── background/                # Heartbeat + subagent worker
├── memory/
│   ├── store.py                   # MemoryStore — SQLite 15 tables
│   └── models.py                  # Pydantic models
└── rag/                           # Optional FAISS retriever

gbot_cli/                          # CLI package (separate module)
├── commands.py                    # Typer CLI entry points
├── client.py                      # GraphBotClient (httpx)
├── credentials.py                 # Token storage (~/.graphbot/)
├── repl.py                        # Interactive REPL
├── slash_commands.py              # Slash command router
└── output.py                      # Rich formatters
```

## SQLite Tables (15)

| Table | Purpose |
|-------|---------|
| `users` | User records |
| `user_channels` | Channel links (telegram, discord, ...) |
| `sessions` | Chat sessions with token tracking |
| `messages` | Chat messages |
| `agent_memory` | Key-value long-term memory |
| `user_notes` | Learned information about users |
| `activity_logs` | Activity records |
| `favorites` | User favorites |
| `preferences` | User preferences (JSON) |
| `cron_jobs` | Scheduled tasks |
| `cron_execution_log` | Cron execution history |
| `reminders` | One-shot reminders |
| `system_events` | Background notification queue |
| `background_tasks` | Subagent task records |
| `api_keys` | API key management |

## Workspace

```
workspace/
├── AGENT.md              # Bot identity (system prompt)
├── HEARTBEAT.md          # Heartbeat instructions (optional)
└── skills/               # User skills (optional)
```

`AGENT.md` defines the bot's personality and behavior. `system_prompt` in config takes higher priority.

---

## Development

```bash
uv sync --extra dev                    # install dependencies
uv run pytest tests/ -v                # run tests
uv run ruff check graphbot/ gbot_cli/  # lint
uv run ruff format graphbot/ gbot_cli/ # format
gbot run --reload                      # dev server with auto-reload
```

226 tests (206 unit + 20 integration), 15 test files.

## Technologies

| Component | Technology |
|-----------|------------|
| Agent | LangGraph StateGraph |
| LLM | LiteLLM (multi-provider) |
| API | FastAPI + Uvicorn |
| Memory | SQLite (WAL mode) |
| Config | YAML + pydantic-settings + .env |
| Background | APScheduler |
| CLI | Typer + Rich + prompt_toolkit |
| RAG | FAISS + sentence-transformers |
| Container | Docker + docker-compose |
| Lint | Ruff |
| Package | uv |

## License

MIT — see [LICENSE](./LICENSE) for details.

