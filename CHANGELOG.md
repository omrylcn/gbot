# Changelog

All notable changes to this project will be documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


## [1.6.0] - 2026-02-15

### Added (CLI Enhancement — API Client + Rich REPL)

- **`gbot` CLI entry point:** Terminal command renamed `graphbot` → `gbot` (`graphbot` kept as alias)
- **`gbot_cli/` package:** CLI code moved from `graphbot/cli/` to a separate `gbot_cli/` package
- **GraphBotClient:** Sync httpx wrapper for all API endpoints (health, chat, login, sessions, user, admin)
- **Credentials:** Token storage at `~/.graphbot/credentials.json` with `chmod 0600`
- **Interactive REPL:** Rich-rendered chat shell with robot logo, markdown output, spinner, auto session management
- **Slash command autocomplete:** Real-time `/` completion via `prompt_toolkit`
- **Slash commands:** `/help`, `/status`, `/session`, `/history`, `/context`, `/config`, `/skill`, `/cron`, `/user`, `/events`, `/clear`, `/exit`
- **Rich formatters:** Table/panel renderers for sessions, users, crons, skills, config, events, history
- **Admin API:** `GET /admin/status`, `/admin/config`, `/admin/skills`, `/admin/users`, `/admin/crons`, `/admin/logs`, `DELETE /admin/crons/{job_id}` (owner-only)
- **`login` / `logout` commands:** Save/clear credentials for API authentication
- **Default REPL:** `gbot` (bare, no arguments) opens REPL directly
- **System user fallback:** When not logged in, uses `getpass.getuser()` for OS username
- **23 new tests** (test_cli_client.py, test_cli_repl.py, test_admin_api.py)

### Changed

- **`chat` command reworked:** Defaults to API-backed REPL mode; `--local` flag preserves standalone mode; `--server`, `--token`, `--api-key` flags for connection config; `-m` for single-shot API calls
- **`app.py`:** Admin router registered
- **`graphbot/cli/` removed:** All CLI code moved to `gbot_cli/` package, old directory deleted

## [1.5.0] - 2026-02-15

### Added (Docker & Deploy)

- **Dockerfile:** `uv:python3.11-bookworm-slim` base image, `.[channels]` included, healthcheck, `graphbot` CLI entrypoint
- **docker-compose.yml:** Single service, named volumes (`graphbot_data`, `graphbot_workspace`), `config.yaml` read-only bind mount, `.env` env_file
- **.dockerignore:** Excludes `reference files/`, `data/`, `.venv/`, `tests/`, `gbot/` etc. from container

### Fixed

- **config.yaml:** Fixed `channels.telegram.enabled: true1` typo → `true`

## [1.4.0] - 2026-02-15

### Added (Delegation Planner)

- **DelegationPlanner:** Single LLM call to plan subagent execution — picks tools, prompt, and model automatically based on task description
- **DelegationConfig:** New config section (`background.delegation`) with `model` and `temperature` fields for the planner LLM
- **Tool Registry:** `build_background_tool_registry()` — shared name→tool mapping for background agents (subagent + cron), excludes meta/unsafe tools (delegate, cron, reminder, shell)
- **`resolve_tools()`:** Centralized tool name→object resolution, replaces duplicate logic in worker and scheduler
- **`get_tool_catalog()`:** Human-readable tool catalog string for the delegation planner prompt
- **17 new tests** (test_delegation.py) — registry, resolve, catalog, planner parse, scheduler fix, delegate+planner integration

### Changed

- **Delegate tool simplified:** Main LLM now only calls `delegate(user_id, task)` — planner decides tools/prompt/model instead of main agent
- **SubagentWorker:** Uses shared tool registry (built once in `__init__`), accepts `prompt` parameter from planner, explicit model fallback to `config.assistant.model`
- **CronScheduler._parse_tools:** Now uses shared tool registry instead of returning empty list — cron jobs with `agent_tools` can now resolve tools correctly
- **make_tools():** Added `planner` parameter, passed to `make_delegate_tools(worker, planner)`
- **app.py lifespan:** Creates registry + catalog + planner, wires into tool factory

### Fixed

- **CronScheduler `_parse_tools()` always returned `[]`:** Cron/alert jobs using LightAgent couldn't access tools (e.g. web_search for price alerts). Now resolved via shared registry.
- **`model=null` crash in LightAgent:** When planner LLM returned `"model": "null"` (string instead of JSON null), LightAgent passed literal `"null"` to litellm causing BadRequestError. Fixed with string sanitization in `_parse()` + explicit model fallback in worker and scheduler.

## [1.3.0] - 2026-02-15

### Added (WebSocket Events & Recurring Reminders)

- **Recurring reminders:** `cron_expr` column on reminders table — periodic reminders via CronTrigger, stays "pending" (not marked sent)
- **`create_recurring_reminder` tool:** Create periodic reminders with cron expressions, no LLM processing
- **WebSocket event delivery:** `ConnectionManager` class for real-time push of cron/reminder/subagent results to connected clients
- **WS fallback:** If no WebSocket connected, events stored in `system_events` table for polling/context injection

### Changed

- **SubagentWorker → LightAgent:** Subagents now use lightweight LightAgent instead of full GraphRunner — isolated context, restricted tools, model override
- **Delegate tool:** Added `tools` and `model` parameters — main agent decides subagent capabilities at delegation time
- **`_resolve_tools()`:** Converts tool name strings to actual tool objects for subagent (web_search, web_fetch, search_items, save_user_note)
- **CronScheduler._send_to_channel:** WS push for API channel + DB fallback when not connected
- **SubagentWorker._run:** WS push on task completion + mark_events_delivered
- **`list_reminders` tool:** Shows recurring info (cron expression) for periodic reminders

### Fixed

- **`make_search_tools` call:** Fixed wrong argument count in `_resolve_tools()` — was passing `(config, db)`, function takes `(retriever)`

## [1.2.0] - 2026-02-15

### Added (LightAgent & Background Task Refactoring)

- **LightAgent:** Lightweight, isolated agent for background tasks — own prompt, restricted tools, model override, no context loading
- **NOTIFY/SKIP:** LLM response markers (SKIP, [SKIP], [NO_NOTIFY]) to suppress unnecessary cron notifications
- **skip_context:** Flag in GraphRunner.process() to load identity-only prompt for background tasks
- **create_alert tool:** Cron job with NOTIFY/SKIP template — only notifies when something needs attention
- **Execution log:** cron_execution_log table tracks every cron run (result, status, duration_ms)
- **Failure tracking:** consecutive_failures counter on cron jobs, auto-pause after 3 failures
- **Standalone reminders table:** Separate from cron_jobs, with status tracking (pending/sent/failed/cancelled) and retry logic
- **system_events table:** Event queue for background → agent communication, injected into context on next user session
- **background_tasks table:** SubagentWorker results persisted to DB + system_event created on completion
- **Agent params on cron jobs:** agent_prompt, agent_tools, agent_model, notify_condition columns

### Changed

- CronScheduler._execute_job() uses LightAgent when agent_prompt is set, falls back to full runner
- SubagentWorker now accepts optional db parameter for result persistence
- Reminders use standalone table (not cron_jobs), no LLM involved
- ContextBuilder injects undelivered system_events as "Background Notifications" layer
- make_cron_tools() returns 4 tools (was 3, added create_alert)

## [1.0.0] - 2025-02-07

### Added (Initial Release)

- **Core:** LangGraph-based stateless agent with 4-node graph (load_context → reason ⇄ execute_tools → respond)
- **Core:** GraphRunner orchestrator — SQLite ↔ LangGraph bridge, request-scoped
- **Core:** Config system — YAML + pydantic-settings + .env overlay
- **Core:** LiteLLM multi-provider support (OpenAI, Anthropic, DeepSeek, Groq, Gemini, OpenRouter)
- **Memory:** SQLite store with 10 tables (users, sessions, messages, agent_memory, user_notes, activity_logs, favorites, preferences, user_channels, cron_jobs)
- **Memory:** ContextBuilder with 6 layers (identity, agent_memory, user_ctx, prev_summary, skills, skills_index)
- **Memory:** Token-based sessions (30k limit, LLM summary on transition)
- **API:** FastAPI service with chat, sessions, health, user context endpoints
- **API:** Token-based authentication (register/login)
- **API:** WebSocket support for real-time chat
- **Channels:** Telegram, Discord, WhatsApp, Feishu multi-channel support
- **Tools:** 9 tool groups — memory, notes, favorites, activity, filesystem, shell, web search, cron, sub-agent
- **Skills:** YAML-based skill system with requirements checking and dynamic index
- **Background:** Cron scheduler for reminders/alarms with proactive messaging
- **Background:** Heartbeat service and sub-agent worker
- **RAG:** Optional FAISS-based retrieval with multilingual embeddings
- **CLI:** Typer-based CLI with interactive chat, config check, version commands
