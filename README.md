<p align="center">
  <img src="images/gbot_logo.svg" alt="GBot Logo" width="400">
</p>

# GBot

Extensible AI assistant framework built on LangGraph.

Multi-channel support, long-term memory, background tasks, tool system, admin dashboard, and an interactive CLI — all backed by SQLite as the single source of truth.

## What is this project for?

GBot is designed to help you build a **production-ready personal/team assistant** that can move beyond plain chat:

- Persist conversation state and user memory in a simple local database (SQLite)
- Run tool-augmented workflows (files, shell, web, reminders, cron jobs, delegation)
- Serve users through API, CLI, WebSocket, and messaging channels from one core runtime
- Keep the agent loop stateless while maintaining durable operational history and tasks

In short: this project aims to be a practical assistant platform you can run, extend, and operate without heavyweight infrastructure.

## Quick Start

**Prerequisites:** Python 3.11+, [uv](https://docs.astral.sh/uv/)

### 1. Install

```bash
git clone https://github.com/omrylcn/gbot.git
cd gbot
uv sync --extra dev
```

### 2. Configure

```bash
cp config/config.example.yaml config/config.yaml   # your local config (gitignored)
cp .env.example .env                               # your API keys (gitignored)
```

Edit `.env` — you need at least one LLM provider:
```bash
OPENROUTER_API_KEY=sk-or-...    # recommended (access to many models)
# or: OPENAI_API_KEY=sk-...
# or: ANTHROPIC_API_KEY=sk-...
```

Edit `config/config.yaml` — set your owner info and preferred model:
```yaml
assistant:
  name: "GBot"
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
GBOT_PROVIDERS__OPENAI__API_KEY=sk-...
```

### 3. Run

```bash
gbot run                    # start API server on :8000
gbot                        # open interactive REPL
```

That's it. The REPL connects to the API server automatically.

---

## Architecture

GBot deliberately separates the "thinking" from the "remembering." LangGraph handles the agent loop as a pure execution engine — no checkpoints, no internal state. All durable state lives in SQLite: sessions, memory, users, scheduled tasks, events. This means you can restart the server, swap models, or scale horizontally without losing anything.

| Principle | Description |
|-----------|-------------|
| **LangGraph = stateless** | No checkpoint — used purely as an execution engine |
| **SQLite = source of truth** | 15 tables for sessions, memory, users, tasks, events, relations |
| **GraphRunner = orchestrator** | Request-scoped bridge between SQLite and LangGraph |
| **LiteLLM = multi-provider** | OpenAI, Anthropic, DeepSeek, Groq, Gemini, OpenRouter |

The agent graph has 4 nodes: `load_context` → `reason` ⇄ `execute_tools` → `respond`

---

## Features

GBot isn't just a chatbot wrapper — it's a full operational platform. Here's what's built in and working:

| Feature | Description |
|---------|-------------|
| Multi-provider LLM | 6+ providers via LiteLLM + direct OpenRouter SDK |
| Multi-channel | Telegram (active), WhatsApp via WAHA (active), Discord/Feishu (stub) |
| Long-term memory | Typed memory facts (semantic/episodic/preference/procedural) with AUDN update, semantic search (sqlite-vec), entity relations, temporal notes |
| Session management | Token-limit based with automatic LLM summary on transition |
| RBAC | 3 roles (owner/member/guest), roles.yaml, 2-layer guard |
| 8 tool groups (26 tools) | Memory (incl. search_memory, forget_fact, what_do_you_know), search, filesystem, shell, web, messaging, delegation, skills |
| Skill system | Markdown-based, progressive disclosure via `load_skill` tool |
| Agent profiles | `agents.yaml` — per-profile AGENT.md, skills, context layers |
| Context service | 7-layer system prompt with description/source metadata |
| Background tasks | Unified task scheduler (recurring/delayed/immediate/monitor), heartbeat, async subagents |
| Delegation | LLM-based planner — immediate, delayed, recurring, monitor |
| Interactive CLI | `gbot` — Rich REPL with slash commands and autocomplete |
| Admin API | Server status, config, skills, tools, users, stats, tasks, context, logs |
| Admin Dashboard | React web UI — context inspector, conversations, users, tools, tasks, memory inspector |
| RAG | Optional FAISS + sentence-transformers semantic search |
| WebSocket | Real-time chat + event delivery |
| Docker | Single-command deployment with docker-compose |

Most of these work together. For example, a user on Telegram can say "remind me every morning at 9 to check gold prices" — the delegation planner creates a recurring cron job, the LightAgent runs it with web tools, and the result gets delivered back through Telegram. No manual wiring needed.

---

## Usage

GBot gives you four ways to interact: a CLI for quick operations, a REPL for interactive sessions, a REST API for integration, and an admin dashboard for monitoring. They all talk to the same backend — pick whichever fits your workflow.

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
gbot login ali -s http://localhost:8000  # saves token to ~/.gbot/
gbot logout
```

Tasks:
```bash
gbot cron list               # list recurring/monitor tasks
gbot cron remove <task_id>   # remove a task
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
| `/cron list\|remove <id>` | Task management |
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
| GET | `/session/{sid}/stats` | Session stats (messages, tokens, context) |
| POST | `/session/{sid}/end` | End session |
| GET | `/user/{user_id}/context` | User context |
| POST | `/auth/register` | Register (owner-only) |
| POST | `/auth/login` | Login |
| POST | `/auth/token` | OAuth2 token endpoint |
| GET | `/auth/user/{user_id}` | User profile |
| POST | `/auth/api-keys` | Create API key |
| GET | `/auth/api-keys` | List API keys |
| DELETE | `/auth/api-keys/{key_id}` | Deactivate API key |
| WS | `/ws/chat` | WebSocket chat + event delivery |
| POST | `/webhooks/telegram/{user_id}` | Telegram webhook |
| POST | `/webhooks/whatsapp/{user_id}` | WhatsApp webhook (WAHA) |

### Admin Endpoints (owner-only)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/admin/status` | Server status |
| GET | `/admin/config` | Sanitized configuration |
| GET | `/admin/skills` | Skill list |
| GET | `/admin/tools` | Registered tools (ToolRegistry introspection) |
| GET | `/admin/users` | User list with roles |
| PUT | `/admin/users/{user_id}/role` | Set user role |
| GET | `/admin/tasks` | Background tasks (filter by execution_type, status) |
| DELETE | `/admin/tasks/{task_id}` | Cancel/remove task |
| GET | `/admin/stats` | System stats (context, tools, sessions, data) |
| GET | `/admin/logs` | Execution logs |
| GET | `/admin/memory/{user_id}` | Memory facts, stats, relations, processing log |
| GET | `/admin/context/{profile}/layers` | Context layers for profile (main/planner/light) |
| GET | `/admin/context/{profile}/preview` | Full context preview for profile |
| GET | `/admin/context/budget` | Token budget breakdown |
| GET | `/admin/context/overrides` | Runtime layer overrides |
| POST | `/admin/context/overrides` | Set runtime layer overrides |
| DELETE | `/admin/context/overrides/{layer}` | Delete layer override |
| GET | `/admin/context/profiles` | Agent profile list |
| GET | `/admin/context/profiles/{name}` | Agent profile detail |

### Tool Usage (via Chat)

You don't call tools by name — just describe what you want. The LLM figures out which tool to use:

```
"Save this note: meeting tomorrow"                   → save_user_note
"Set a reminder in 5 minutes: take medicine"          → delegate (delayed/static)
"Check the weather every morning at 9"                → delegate (recurring/agent)
"Alert me if gold goes above $2000"                   → delegate (monitor/agent)
"Do this in the background: research topic X"         → delegate (immediate/agent)
"What do you know about me?"                          → what_do_you_know
"Search your memory for coffee preferences"           → search_memory
"Forget that I live in Istanbul"                      → forget_fact
```

---

## Configuration

GBot uses a layered config system — you can set things in multiple places, and the most specific one wins:

Priority order: `.env` > environment variables > `config.yaml` > defaults

```bash
# .env uses GBOT_ prefix with __ separator
GBOT_ASSISTANT__MODEL=openai/gpt-4o-mini
GBOT_PROVIDERS__OPENAI__API_KEY=sk-...
GBOT_BACKGROUND__CRON__ENABLED=true
```

Full config reference: [`config/config.example.yaml`](./config/config.example.yaml)

### Authentication

Authentication is optional — you can run GBot wide open for local development, or lock it down with JWT + API keys for production. A single config value controls the switch:

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
GBOT_AUTH__JWT_SECRET_KEY=your-secret-key-at-least-32-characters
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

**CLI login** — saves token to `~/.gbot/credentials.json`:

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

Tools are organized into 8 groups (26 tools total). By default all groups are enabled (`tools: ["*"]`), but RBAC can restrict which groups each role can access. The `ToolRegistry` resolves group names to actual tool functions automatically via `roles.yaml`:

| Group | Tools | Description |
|-------|-------|-------------|
| Memory | save_user_note, get_user_context, add_favorite, get_favorites, remove_favorite, set_user_preference, get_user_preferences, remove_user_preference, search_memory, forget_fact, what_do_you_know | User memory + semantic search (11 tools) |
| Search | search_items, get_item_detail, get_current_time | RAG semantic search + time (3 tools) |
| Filesystem | read_file, write_file, edit_file, list_dir | Workspace files (4 tools) |
| Shell | exec_command | Safe shell — destructive commands blocked (1 tool) |
| Web | web_search, web_fetch | Brave Search + page fetch (2 tools) |
| Messaging | send_message_to_user | Cross-channel message delivery (1 tool) |
| Delegation | delegate, list_scheduled_tasks, cancel_scheduled_task | Background subagent spawn + task management (3 tools) |
| Skills | load_skill | Progressive disclosure — load skill on demand (1 tool) |

Cron jobs, reminders, and alerts are managed through the delegation system — the LLM-based planner decides the execution strategy (immediate, delayed, recurring, or monitor).

### Skill System

Skills are Markdown plugins injected into the system prompt. Unlike always-loaded prompts, skills use **progressive disclosure** — the agent sees skill descriptions but loads full content on demand via the `load_skill` tool. Drop a `.md` file into `workspace/skills/` and it overrides built-in skills of the same name:

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

- `always: true` → always included in system prompt (no `load_skill` needed)
- `always: false` (default) → description shown, content loaded on demand
- Requirements check: skill disabled if binary/env var is missing
- Per-profile skill config via `agents.yaml`: `skills: ["*"]` (all) or `skills: []` (none)

### Agent Profiles

Agent profiles define per-agent-type configuration — which AGENT.md to use, which skills to enable, and which context layers to build. Profiles are defined in `config/agents.yaml`:

```yaml
agents:
  main:
    agent_md: workspace/AGENT.md
    skills: ["*"]
    context_layers: ["*"]      # all 7 layers
  planner:
    agent_md: workspace/agents/planner/AGENT.md
    skills: []
    context_layers: [identity]  # minimal context
    template_vars: [tool_catalog, extra_examples]
  light:
    agent_md: workspace/agents/light/AGENT.md
    skills: []
    # No context_layers — LightAgent has its own context model
  memory:
    agent_md: workspace/agents/memory/AGENT.md
    skills: []
    context_layers: [identity]
```

### Context Service

The context service builds a layered system prompt for each agent profile. Each layer has metadata (description, source) for full traceability in the admin dashboard:

| Layer | Description | Source |
|-------|-------------|--------|
| identity | Agent personality and instructions | AGENT.md |
| runtime | Current time, model, session info | Runtime state |
| role | RBAC role description and permissions | roles.yaml |
| agent_memory | Long-term learned facts about users | agent_memory table |
| user_context | Explicit notes + learned memory facts (2-stage semantic retrieval with re-ranking) | user_notes + memory_facts (sqlite-vec) |
| session_summary | Summary from previous sessions | sessions table |
| skills | Available skill descriptions | workspace/skills/ + builtins |

Runtime overrides can enable/disable layers or inject custom content via the admin API.

### Background Services

GBot can do things even when nobody is chatting. Five services run alongside the API server, each with a different responsibility:

| Service | What it does |
|---------|-------------|
| **CronScheduler** | Loads jobs and reminders from SQLite into APScheduler. Manages both recurring cron jobs (CronTrigger) and one-shot reminders (DateTrigger). Handles execution, delivery, SKIP/NOTIFY logic, and auto-pauses jobs after 3 consecutive failures. |
| **LightAgent** | Stripped-down LangGraph agent for background tasks — no session, no context loading, just a prompt + restricted tool set. Can override the model (e.g. cheaper model for monitoring). Used by both CronScheduler and SubagentWorker. |
| **SubagentWorker** | Spawns async background tasks via `asyncio.create_task`. Persists status to `background_tasks` table and injects delivery notes into the user's active session so the main agent sees the outcome. |
| **HeartbeatService** | Periodic wake-up loop. Reads `HEARTBEAT.md` from workspace — if it has actionable content, triggers the full GraphRunner. Otherwise stays silent. |

The scheduler has 4 processor types that determine *how* a job executes:

| Processor | Execution | LLM needed? |
|-----------|-----------|-------------|
| `static` | Delivers a plain text message directly | No |
| `function` | Calls a specific tool with known arguments | No |
| `agent` | Runs LightAgent with selected tools | Yes (can use cheaper model) |
| `runner` | Invokes full GraphRunner with all context, memory, and tools | Yes (main model) |

**Delivery chain:** When a job fires, the scheduler tries to deliver the result directly to the user's channel (Telegram, WhatsApp, WebSocket). If no active connection exists, it falls back to saving a `system_event` in the database — the ContextBuilder picks it up on the user's next message.

```
User: "Do this in the background: research topic X"
  → delegate tool → DelegationPlanner (LLM) → plan: {execution: immediate, processor: agent}
  → SubagentWorker.spawn() → LightAgent runs with web tools
  → Result → background_tasks table + session note + channel delivery
```

### Memory Layer

GBot's memory goes beyond simple note storage — it automatically learns from conversations, detects contradictions, and serves the most relevant facts when needed.

**How it works:**

```
User sends message
  → Every 5 messages: hot-path extraction (async, fire-and-forget)
    → LLM extracts typed facts + entity relations
    → Each fact: embed (OpenRouter) → search similar (sqlite-vec) → AUDN decision
    → ADD / UPDATE / DELETE / NOOP — LLM decides conflicts
  → ContextBuilder: embed last message → search top 20 → re-rank → top 10 to context
```

**Three tables work together:**

| Table | Purpose |
|-------|---------|
| `memory_facts` | Typed facts with embedding, confidence, importance, category |
| `memory_relations` | Entity relationships (works_at, married_to, owns...) |
| `memory_processing_log` | Extraction audit trail |

**AUDN (Add/Update/Delete/Noop):** When a new fact is extracted, similar existing facts are found via semantic search. An LLM then decides: is this new info (ADD), an update to existing info (UPDATE), a negation (DELETE), or already known (NOOP)?

```
"İstanbul'da yaşıyorum"  → ADD (new fact)
"Ankara'ya taşındım"     → UPDATE (İstanbul invalidated, Ankara added)
"Artık borsa takip etmiyorum" → DELETE (borsa fact invalidated, no new fact)
"Hala İstanbul'dayım"    → NOOP (already known)
```

**2-stage retrieval:** Context doesn't get a flat list of all facts. Instead: (1) sqlite-vec finds 20 semantically similar candidates, (2) re-ranker scores them by `similarity × recency × access_count × confidence`, (3) top 10 enter context. Frequently accessed facts score higher; old unused facts fade out.

**Configuration:**

```yaml
memory:
  enabled: true
  extraction_every_n: 5        # extract every N user messages
  embedding:
    model: "google/gemini-embedding-001"
    dimension: 3072
  update:
    strategy: "llm"            # embedding finds, LLM decides
```

### RAG (Optional)

If you have structured data (products, documents, articles) that you want the agent to search through, enable the RAG module. It builds a local FAISS index and adds semantic search tools automatically:

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

Telegram integration follows a "one bot per user" model — each user creates their own bot via @BotFather and links the token. This keeps things simple and avoids multi-tenant bot routing:

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

### WhatsApp (WAHA)

WhatsApp integration uses [WAHA](https://waha.devlike.pro/) (WhatsApp HTTP API) as a Docker sidecar. Unlike Telegram, this connects your actual WhatsApp account — the bot reads and responds through your phone number:

```yaml
# config.yaml
channels:
  whatsapp:
    enabled: true
    waha_url: "http://waha:3000"
    session: "default"
    api_key: "your-waha-api-key"
    respond_to_dm: true
    monitor_dm: false
    allowed_groups:
      - "120363xxx@g.us"
    allowed_dms:
      "905551234567": "Ali"
```

```bash
# 1. WAHA runs as a Docker service (see docker-compose.yml)
# 2. Configure whatsapp section in config.yaml
# 3. Pair your phone via WAHA dashboard (http://localhost:3000)
# 4. Start the server — webhook is auto-registered
gbot run
```

Features:
- Supports both phone numbers (`@c.us`) and Linked IDs (`@lid`)
- Group and DM whitelist filtering
- `respond_to_dm` — reply to direct messages
- `monitor_dm` — log but don't reply

### RBAC (Role-Based Access Control)

Not every user should have access to everything. RBAC lets you define who can use which tools and see which context. There are 3 built-in roles, enforced at two points: once when the LLM is deciding which tools to consider (reason filter), and again before any tool actually executes (execute guard):

| Role | Tool Groups | Context Layers | Sessions |
|------|-------------|---------------|----------|
| **owner** | all 8 groups | all 7 layers | unlimited |
| **member** | memory, search, web, messaging, delegation | all 7 layers | unlimited |
| **guest** | web only | identity, runtime, role | max 1 |

Default role: `guest`. Set user role via `PUT /admin/users/{user_id}/role`.

`roles.yaml` defines role → group mapping; tool names are resolved automatically from `ToolRegistry`.

### Delegation System

This is where GBot gets interesting. Instead of hardcoding "reminder = delayed message" and "cron = scheduled job," there's an LLM-based planner (`DelegationPlanner`) that figures out the best execution strategy for any background request. You just describe what you want, and it picks the right combination:

| Execution | Description |
|-----------|-------------|
| `immediate` | Run now in the background |
| `delayed` | Run after N seconds (one-shot reminder) |
| `recurring` | Run on a cron schedule |
| `monitor` | Periodic check with NOTIFY/SKIP logic |

| Processor | Description |
|-----------|-------------|
| `static` | Direct text message (no LLM) |
| `function` | Single tool call |
| `agent` | LightAgent with selected tools |
| `runner` | Full GraphRunner with all context |

```
User: "Remind me in 5 minutes: take medicine"
  → DelegationPlanner → {execution: "delayed", processor: "static", delay: 300}
  → CronScheduler creates one-shot job → delivers message after 5 min

User: "Check gold price every morning at 9, alert if above $2000"
  → DelegationPlanner → {execution: "recurring", processor: "agent", cron: "0 9 * * *"}
  → CronScheduler creates recurring job → LightAgent evaluates → NOTIFY or SKIP
```

---

## Docker

The easiest way to deploy GBot in production. Everything — API server, dashboard, WAHA (WhatsApp), volumes — is defined in a single compose file:

```bash
docker compose up -d         # start all services
docker compose logs -f       # follow logs
docker compose down          # stop
```

| Service | Port | Description |
|---------|------|-------------|
| `gbot` | 8000 | API server |
| `dashboard` | 3001 | Admin dashboard (React + nginx) |
| `waha` | 3000 | WhatsApp gateway (optional) |

The dashboard is optional — it proxies API requests through nginx (`/api/` → gbot:8000) and runs independently. Uses named volumes (`gbot_data`, `gbot_workspace`) and `config.yaml` as read-only bind mount.

---

## Project Structure

The codebase is split into two packages: `gbot` (the core framework) and `gbot_cli` (the CLI client). They're independent — the CLI talks to the framework over HTTP, so you could swap it for your own client.

```
gbot/                          # Core framework
├── agent/
│   ├── context/                   # Context service package
│   │   ├── builder.py             # ContextBuilder (10-layer system prompt)
│   │   ├── models.py              # LayerResult, ContextBudget models
│   │   └── service.py             # ContextService facade (admin API)
│   ├── graph.py                   # StateGraph compile
│   ├── nodes.py                   # 4 nodes: load_context, reason, execute_tools, respond
│   ├── runner.py                  # GraphRunner orchestrator
│   ├── light.py                   # LightAgent (background tasks)
│   ├── state.py                   # AgentState(MessagesState)
│   ├── profiles.py                # Agent profiles (agents.yaml loader)
│   ├── skills/                    # Skill loader + built-ins
│   ├── delegation.py              # DelegationPlanner (LLM-based task routing)
│   ├── permissions.py             # RBAC — roles.yaml loader, tool/context filtering
│   └── tools/                     # 8 tool groups, 23 tools (ToolRegistry)
├── api/
│   ├── app.py                     # FastAPI app + lifespan
│   ├── routes.py                  # Chat, health, sessions, user endpoints
│   ├── admin.py                   # Admin endpoints (owner-only, 18 endpoints)
│   ├── auth.py                    # JWT + API key auth
│   ├── ws.py                      # WebSocket chat
│   └── deps.py                    # Dependency injection
├── core/
│   ├── config/                    # YAML + BaseSettings + .env
│   ├── providers/                 # LiteLLM + OpenRouter SDK
│   ├── channels/                  # Telegram, WhatsApp (WAHA), base channel
│   ├── cron/                      # APScheduler + types
│   └── background/                # Heartbeat + subagent worker
├── memory/
│   ├── store.py                   # MemoryStore — SQLite 12 tables
│   └── models.py                  # Pydantic models
└── rag/                           # Optional FAISS retriever

gbot_cli/                          # CLI package (separate module)
├── commands.py                    # Typer CLI entry points
├── client.py                      # GraphBotClient (httpx)
├── credentials.py                 # Token storage (~/.gbot/)
├── repl.py                        # Interactive REPL
├── slash_commands.py              # Slash command router
└── output.py                      # Rich formatters

config/                            # Configuration files
├── config.example.yaml            # Config template (committed)
├── config.yaml                    # Local config (gitignored)
├── agents.yaml                    # Agent profiles (main/planner/light)
└── roles.yaml                     # RBAC role definitions

dashboard/                         # Admin dashboard (React + Vite)
├── src/
│   ├── pages/                     # Dashboard, Context, Conversations, Users, Tools, Tasks
│   ├── components/                # Layout + shared components
│   ├── api/                       # API client + admin/auth endpoints
│   └── stores/                    # Zustand (auth, theme)
├── Dockerfile                     # nginx:alpine production build
└── nginx.conf                     # API proxy configuration
```

## SQLite Tables (12)

Everything lives in a single SQLite file. No Postgres, no Redis, no external dependencies. WAL mode is enabled for concurrent reads:

| Table | Purpose |
|-------|---------|
| `users` | User records |
| `user_channels` | Channel links (telegram, whatsapp, ...) |
| `sessions` | Chat sessions with token tracking |
| `messages` | Chat messages |
| `agent_memory` | Key-value long-term memory |
| `user_notes` | Learned information about users |
| `favorites` | User favorites |
| `preferences` | User preferences (JSON) |
| `background_tasks` | Unified task table (immediate/delayed/recurring/monitor) |
| `task_executions` | Task execution audit log |
| `system_events` | Delivery queue for API/WS channels |
| `api_keys` | API key management |

## Workspace

The `workspace/` directory is where you customize the bot's personality and capabilities without touching code:

```
workspace/
├── AGENT.md              # Main agent identity (system prompt)
├── agents/
│   ├── planner/AGENT.md  # Delegation planner identity
│   └── light/AGENT.md    # Background agent identity
├── HEARTBEAT.md          # Heartbeat instructions (optional)
└── skills/               # User skills (optional)
```

Each agent profile (`config/agents.yaml`) points to its own `AGENT.md`. The main agent's is the most important — it defines who the bot is, how it talks, and what it knows. Planner and light agents have minimal, task-focused identities.

---

## Development

```bash
uv sync --extra dev                    # install dependencies
uv run pytest tests/ -v                # run tests
uv run ruff check gbot/ gbot_cli/  # lint
uv run ruff format gbot/ gbot_cli/ # format
gbot run --reload                      # dev server with auto-reload
```


## Technologies

| Component | Technology |
|-----------|------------|
| Agent | LangGraph StateGraph |
| LLM | LiteLLM + OpenRouter SDK (multi-provider) |
| API | FastAPI + Uvicorn |
| Dashboard | React 19 + TanStack Query + Zustand + Tailwind CSS 4 |
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

