<p align="center">
  <img src="images/gbot_logo.svg" alt="GBot Logo" width="400">
</p>

# GBot

Extensible AI assistant framework built on LangGraph. Personal or team scale, multi-channel (API, CLI, Telegram, WhatsApp), with long-term memory, scheduled tasks, a tool system, and an admin dashboard. Everything is backed by a single SQLite file — no Postgres, no Redis, no external services.

```
You ──► chat / Telegram / WhatsApp / REPL
         │
         ▼
   FastAPI ── GraphRunner ── LangGraph (stateless agent loop)
         │                     │
         │                     └─► tools (file, shell, web, memory, delegate, …)
         ▼
      SQLite (sessions · messages · memory · tasks · users · embeddings)
```

> **TL;DR for the impatient:** clone, set one API key, run `uv run gbot run`, talk to it. Five minutes from zero to working bot. See [Quick Start](#quick-start).

---

## Table of Contents

- [Quick Start](#quick-start) — clone → run → first message (5 min)
- [Configuration](#configuration) — `.env`, `config.yaml`, env-var overrides
- [Authentication](#authentication) — disabled by default; how to turn it on
- [Users & CLI](#users--cli) — adding users, login, REPL, slash commands
- [Channels](#channels) — Telegram (recommended), WhatsApp (optional)
- [Memory Layer](#memory-layer) — typed facts, AUDN updates, semantic retrieval
- [Tools](#tools) — what the agent can do
- [Background Tasks](#background-tasks) — delegation, cron, reminders
- [REST API](#rest-api) — endpoints reference
- [Admin Dashboard](#admin-dashboard) — React UI on :3001
- [Docker Deployment](#docker-deployment)
- [Project Structure](#project-structure)
- [Development](#development)

---

## Quick Start

**Prerequisites:** Python 3.11+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).

### 1. Clone and install

```bash
git clone https://github.com/omrylcn/gbot.git
cd gbot
uv sync --extra dev
```

This creates `.venv/`, installs all dependencies, and registers the `gbot` CLI.

### 2. Get an LLM API key

You need at least one. **OpenRouter is recommended** — single key, access to Gemini / GPT / Claude / open-source models, and the default config already targets it.

| Provider | Get a key | Set in `.env` as |
|----------|-----------|------------------|
| **OpenRouter** | https://openrouter.ai/keys | `OPENROUTER_API_KEY` |
| OpenAI | https://platform.openai.com/api-keys | `OPENAI_API_KEY` |
| Anthropic | https://console.anthropic.com/settings/keys | `ANTHROPIC_API_KEY` |

### 3. Configure

```bash
cp config/config.example.yaml config/config.yaml   # local config (gitignored)
cp .env.example .env                               # secrets (gitignored)
```

Open `.env`, paste your key:
```bash
OPENROUTER_API_KEY=sk-or-v1-...
```

That's it. The defaults in `config.yaml` (model `openrouter/google/gemini-3-flash-preview`, owner `owner` with password `owner`) are good for a first run.

### 4. Run

```bash
uv run gbot run
```

You'll see:
```
Starting gbot API on 0.0.0.0:8000
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

Verify in another terminal:
```bash
curl http://localhost:8000/health
# → {"status":"ok","agent_ready":true,"version":"1.18.2","items_count":0}
```

### 5. Send your first message

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"hello","user_id":"owner"}'
# → {"response":"Hi! How can I help?","session_id":"..."}
```

Or open the interactive REPL:
```bash
uv run gbot
```

You're done. The bot is alive, conversation history is persisted, and you can scale up from here.

> **Activate the venv if you prefer:** `source .venv/bin/activate` then drop the `uv run` prefix.

---

## Configuration

Two files. `.env` for secrets, `config.yaml` for everything else. Environment variables always win.

```
priority:  env vars (GBOT_*)  >  .env file  >  config.yaml  >  defaults
```

### `.env` — Secrets

LLM keys, JWT secret, channel tokens. Never commit this file.

```bash
# At least one provider key
OPENROUTER_API_KEY=sk-or-v1-...

# Optional — enable JWT authentication when set (32+ chars)
GBOT_AUTH__JWT_SECRET_KEY=

# Optional — Telegram (only if using channels.telegram.enabled)
TELEGRAM_BOT_TOKEN=

# Optional — WAHA (only if using docker-compose with WhatsApp)
WAHA_API_KEY=
GBOT_CHANNELS__WHATSAPP__API_KEY=
```

### `config.yaml` — Behavior

```yaml
assistant:
  name: "GBot"
  owner:
    username: "owner"
    name: "Owner"
    password: "owner"          # initial password — used when auth is enabled
  model: "openrouter/google/gemini-3-flash-preview"
  temperature: 0.7

channels:
  telegram:
    enabled: true              # webhook lives at /webhooks/telegram/{user_id}
  whatsapp:
    enabled: false             # opt-in (requires WAHA, see Channels section)

memory:
  enabled: true                # typed-fact extraction, AUDN updates, semantic retrieval
  embedding:
    provider: "openrouter"
    model: "google/gemini-embedding-001"

auth:
  jwt_secret_key: ""           # empty = auth disabled. Set via GBOT_AUTH__JWT_SECRET_KEY in .env
  rate_limit:
    enabled: true
    requests_per_minute: 60
```

See `config/config.example.yaml` for every field with comments.

### Overriding any field via env var

The `GBOT_` prefix maps any nested config field. Use `__` (double underscore) for nesting:

```bash
GBOT_ASSISTANT__MODEL=openai/gpt-4o-mini
GBOT_AUTH__JWT_SECRET_KEY=my-32-character-secret-key-here-yes
GBOT_MEMORY__ENABLED=false
```

This is the recommended way to set production secrets — never hardcode them into `config.yaml`.

---

## Authentication

GBot ships with auth **disabled** so the Quick Start works in five minutes. Turn it on for production.

### Auth disabled (default)

`auth.jwt_secret_key` is empty → all endpoints accept requests with `user_id` in the body. No login flow.

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"hi","user_id":"owner"}'
```

### Auth enabled

Set `GBOT_AUTH__JWT_SECRET_KEY` (32+ chars) in `.env` and **restart the server**:

```bash
echo "GBOT_AUTH__JWT_SECRET_KEY=$(openssl rand -hex 32)" >> .env
uv run gbot run    # restart
```

Now every endpoint requires a JWT or API key. Login flow:

```bash
# 1. Login → get token
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"user_id":"owner","password":"owner"}' \
  | jq -r .token)

# 2. Use token for subsequent requests
curl -X POST http://localhost:8000/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"hi"}'
```

The owner password comes from `config.assistant.owner.password` and is hashed into the database on first startup. After that, the config value is ignored — change passwords with `gbot user set-password` (see below).

---

## Users & CLI

### Add a user

Users are managed by the `gbot` CLI, which writes directly to SQLite. No server required.

```bash
# Create a user with password
uv run gbot user add ali --name "Ali" --password "ali123"

# Create a user and link a Telegram bot in one command
uv run gbot user add veli --name "Veli" --password "v123" --telegram "<bot-token>"

# Change password (positional args — no flag)
uv run gbot user set-password ali newpassword

# List all users
uv run gbot user list

# Remove user (and their channel links)
uv run gbot user remove ali

# Link a channel to an existing user
uv run gbot user link ali telegram <bot-token>
```

### Log in from the CLI

```bash
uv run gbot login ali --password ali123
# (or interactive: uv run gbot login ali — it will prompt)
# → "Logged in as ali" — token saved to ~/.gbot/credentials.json
```

If you're using a non-default port:
```bash
uv run gbot login ali -s http://localhost:8765 -p ali123
```

### Chat from the CLI

```bash
# Single message via API (uses saved token)
uv run gbot chat -m "hello"

# Local mode (bypasses the server entirely)
uv run gbot chat --local -m "hello"

# Interactive REPL (rich UI, slash commands, autocomplete)
uv run gbot
```

### REPL slash commands

Inside the REPL:

```
/help              # all commands
/status            # system stats (sessions, tools, tokens)
/session info      # current session
/session new       # start fresh session
/history 20        # last 20 messages
/context           # what the agent sees about you
/cron list         # scheduled tasks
/user              # list users (owner only)
/exit
```

### Connect to a non-default server

The CLI honors `GBOT_API_URL`:
```bash
export GBOT_API_URL=http://my-host:8765
uv run gbot
```

---

## Channels

Talk to your bot from any chat client. Telegram is the simplest; WhatsApp is more involved (requires WAHA).

### Telegram (recommended for first integration)

One bot per user — each user creates their own bot via @BotFather. No multi-tenant routing.

**You'll need:** a public HTTPS URL for the webhook. For local dev install [ngrok](https://ngrok.com/download) and run `ngrok http 8000` in a separate terminal. For production, point your domain at the server.

```bash
# 1. Create a bot via @BotFather on Telegram. Save the token.

# 2. Enable Telegram in config.yaml
#    channels:
#      telegram:
#        enabled: true

# 3. Add a user and link the bot
uv run gbot user add ali --name "Ali" --password "p" --telegram "<bot-token>"

# 4. Start the server
uv run gbot run

# 5. In another terminal, expose port 8000 publicly
ngrok http 8000      # gives you https://xxxx.ngrok-free.app

# 6. Register the webhook with Telegram (one-time per bot)
curl "https://api.telegram.org/bot<bot-token>/setWebhook?url=https://xxxx.ngrok-free.app/webhooks/telegram/ali"

# 7. Open Telegram, message your bot. It will reply.
```

### WhatsApp (optional, advanced)

WhatsApp uses [WAHA](https://waha.devlike.pro/) as a Docker sidecar. Unlike Telegram, this connects your real phone number — the bot reads and responds through your WhatsApp account.

```yaml
# config.yaml
channels:
  whatsapp:
    enabled: true
    waha_url: "http://waha:3000"
    session: "default"
    respond_to_dm: true
    monitor_dm: true
    allowed_groups:
      - "GROUP_ID@g.us"
    allowed_dms:
      "905XXXXXXXXX": "Friend's Name"
```

```bash
# 1. WAHA runs as part of docker-compose.yml — `docker compose up`.
# 2. Pair your phone at http://localhost:3000 (WAHA dashboard).
# 3. The webhook is auto-registered on server start.
```

WhatsApp is **opt-in** and meant for advanced users. Skip it on first install.

---

## Memory Layer

The agent remembers things about each user across sessions. Three layers:

| Layer | What | Where |
|-------|------|-------|
| **Notes / preferences** | Explicit, written by tools (`save_user_note`, `set_user_preference`) | `user_notes` table |
| **Facts** | Typed atomic facts auto-extracted from conversation | `memory_facts` + `vec_memory_facts` (sqlite-vec) |
| **Relations** | Entity → relation → entity (e.g. `Ali → works_at → Acme`) | `memory_relations` |

### How extraction works (AUDN)

Every N messages (default 5) the agent extracts facts from recent conversation, embeds them, and runs an **AUDN** decision against existing facts:

- **ADD** — new fact, no overlap with existing
- **UPDATE** — supersedes an old fact (e.g. "moved to Ankara" replaces "lives in Istanbul")
- **DELETE** — invalidates an old fact without replacement
- **NOOP** — already known

### How retrieval works

When building context for a new message, GBot does a 2-stage search:

1. **Semantic search** — embed the user's message, find top-20 candidates via cosine distance
2. **Re-rank** — `final_score = similarity × recency × access_frequency × confidence`

Top 10 facts are injected into the system prompt. `access_count` is incremented for retrieved facts (so frequently-relevant facts surface faster).

### Memory tools the agent can call

```
search_memory(query)    — semantic search of facts
forget_fact(query)      — invalidate the closest matching fact
what_do_you_know()      — list everything I know about you, by category
```

Disable everything with `memory.enabled: false` if you don't want it.

> **Cost:** ~$0.15/month for personal use (gemini-embedding-001 via OpenRouter). The embedding model is configurable.

---

## Tools

The agent has 26 tools across 8 groups. RBAC roles (`owner`, `member`, `guest`) gate access.

| Group | Tools |
|-------|-------|
| **memory** | save_user_note, get_user_context, add/get/remove_favorite, set/get/remove_user_preference, search_memory, forget_fact, what_do_you_know |
| **filesystem** | read_file, write_file, list_directory |
| **shell** | run_shell |
| **web** | web_fetch, web_search |
| **rag** | rag_search (when RAG enabled) |
| **delegate** | delegate, list_scheduled_tasks, cancel_scheduled_task |
| **messaging** | send_message_to_user, send_notification |
| **skills** | load_skill |

`config/roles.yaml` maps roles to tool groups. `config/agents.yaml` maps agent profiles to system prompts and skill sets.

---

## Background Tasks

The `delegate` tool turns natural-language requests into scheduled work:

```
"remind me in 30 minutes to drink water"
   → delayed task, runs once

"every morning at 9 send me the weather"
   → cron task, runs forever

"check the gold price every 5 minutes and tell me if it goes above 3000"
   → monitor task (recurring, only delivers when condition met)
```

A planner LLM converts the request into a structured plan with execution type (`immediate`/`delayed`/`recurring`/`monitor`) and processor (`static`/`function`/`agent`/`runner`). Tasks live in `background_tasks` and execution history in `task_executions`.

Manage tasks via:
- CLI: `gbot cron list`, `gbot cron remove <id>`
- API: `GET/DELETE /admin/tasks`
- Dashboard: Tasks page

---

## REST API

Auth-disabled mode: pass `user_id` in the body. Auth-enabled: pass `Authorization: Bearer <token>`.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health + version |
| POST | `/chat` | Send a message, get a response |
| GET | `/sessions/{user_id}` | List user's sessions |
| GET | `/session/{sid}/history` | Messages in a session |
| GET | `/session/{sid}/stats` | Token / tool / context stats |
| POST | `/session/{sid}/end` | Manually close a session |
| GET | `/user/{user_id}/context` | Notes, prefs, favorites |
| POST | `/auth/login` | Get JWT token |
| POST | `/auth/register` | Register user (owner only) |
| POST | `/auth/api-keys` | Create API key |
| WS   | `/ws/chat` | Streaming chat + event push |
| POST | `/webhooks/telegram/{user_id}` | Telegram webhook |
| POST | `/webhooks/whatsapp/{user_id}` | WhatsApp (WAHA) webhook |

### Admin endpoints (owner-only, 18 total)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/admin/status` | Server overview |
| GET | `/admin/users` | List users |
| GET | `/admin/tasks` | Background tasks |
| GET | `/admin/memory/{user_id}` | Memory inspector |
| GET | `/admin/context/{profile}/preview` | Rendered context for a profile |
| PUT | `/admin/context/{profile}/layers/{layer}` | Override a context layer at runtime |
| GET | `/admin/tools` | Registered tools + RBAC |
| GET | `/admin/stats` | Comprehensive stats |
| GET | `/admin/logs` | Recent execution logs |
| GET | `/admin/profiles` | Agent profiles + AGENT.md |

Full list at [`gbot/api/admin.py`](gbot/api/admin.py).

---

## Admin Dashboard

A React 19 dashboard ships with the project. Run it from `dashboard/`:

```bash
cd dashboard
npm install
npm run dev
# → http://localhost:3001
```

Or in Docker (`docker-compose up dashboard`).

Pages: Conversations, Context Inspector, Memory, Tools, Tasks, Users.

---

## Docker Deployment

`docker-compose.yml` ships everything: API server, dashboard, WAHA, volumes.

```bash
# 1. Set required env vars
echo "OPENROUTER_API_KEY=sk-or-..." >> .env
echo "WAHA_API_KEY=$(openssl rand -hex 16)" >> .env
echo "GBOT_CHANNELS__WHATSAPP__API_KEY=$WAHA_API_KEY" >> .env

# 2. Start everything
docker compose up -d

# 3. View logs
docker compose logs -f gbot
```

Services:
- `gbot` — API on `:8000`
- `dashboard` — React UI on `:3001`
- `waha` — WhatsApp HTTP API on `:3000` (only if you enable WhatsApp)

---

## Project Structure

```
gbot/                            # Core framework package
├── api/                         # FastAPI app, admin, auth, webhooks, WS
├── agent/                       # LangGraph runner, nodes, tools, context, profiles
├── core/                        # Config, channels (telegram/whatsapp), cron, providers
├── memory/                      # MemoryStore (SQLite), MemoryService (extraction), embedder
└── __version__.py

gbot_cli/                        # CLI package (Typer + Rich)
├── commands.py                  # gbot run / chat / login / status / user / cron
├── repl.py                      # Interactive REPL with rich UI
├── client.py                    # GraphBotClient — sync httpx wrapper
└── slash_commands.py            # /help, /status, /session, /context, ...

config/                          # Configuration files
├── config.example.yaml          # Template — committed
├── config.yaml                  # Your local config — gitignored
├── agents.yaml                  # Agent profiles
└── roles.yaml                   # RBAC mapping

workspace/                       # Bot personality (no code, just markdown)
├── AGENT.md                     # Main agent identity
├── agents/                      # Per-profile AGENT.md files
└── skills/                      # Optional skill packs

dashboard/                       # React 19 admin UI

tests/                           # 337 unit tests + integration + e2e
```

### SQLite tables

| Table | Purpose |
|-------|---------|
| `users` · `user_channels` · `api_keys` | Identity + auth |
| `sessions` · `messages` | Conversations |
| `agent_memory` · `user_notes` | Long-term + temporal memory |
| `memory_facts` · `vec_memory_facts` · `memory_relations` · `memory_processing_log` | Faz 22 memory layer |
| `background_tasks` · `task_executions` | Scheduling |
| `system_events` | WS / API event delivery queue |

WAL mode is on. Single file, single backup.

---

## Development

```bash
uv sync --extra dev                       # install
uv run pytest tests/ -v                   # 337 tests
uv run pytest tests/ -m integration       # requires server on :8000
uv run ruff check gbot/ gbot_cli/         # lint
uv run ruff format gbot/ gbot_cli/        # format
uv run gbot run --reload                  # dev server with auto-reload
```

### Releasing

```bash
# bump version in gbot/__version__.py
# update CHANGELOG.md
git tag v1.x.y
git push origin main --tags
```

### Stack

| Component | Technology |
|-----------|------------|
| Agent loop | LangGraph StateGraph (stateless) |
| LLM | LiteLLM + OpenRouter SDK |
| API | FastAPI + Uvicorn |
| Memory | SQLite (WAL) + sqlite-vec |
| Embeddings | OpenRouter embeddings API |
| Background | APScheduler |
| CLI | Typer + Rich + prompt_toolkit |
| Dashboard | React 19 + TanStack Query + Tailwind 4 |
| Container | Docker + docker-compose |
| Package | uv |

---

## License

MIT — see [LICENSE](./LICENSE).
