# Changelog

All notable changes to this project will be documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


## [1.11.0] - 2026-02-23

### Added (Delegation Refactor & WhatsApp DM)

- **Unified `delegate` tool:** Single tool replaces old delegate/reminder/cron split — routes to worker (immediate), scheduler (delayed/recurring/monitor)
- **3 processor types:** `static` (plain text), `function` (direct tool call, no LLM), `agent` (LightAgent with tools)
- **json_schema structured output:** `response_format` forces valid JSON from planner LLM — eliminates parse failures
- **`list_scheduled_tasks` tool:** Lists active cron jobs and pending reminders
- **`cancel_scheduled_task` tool:** Cancel by `cron:<id>` or `reminder:<id>` prefix
- **`LightAgent.run_with_meta()`:** Returns `(response, tokens, called_tools)` for observability
- **`delegation_log` table:** Records every planner decision (execution, processor, reference_id)
- **WhatsApp DM respond:** `respond_to_dm=true` + `allowed_dms` whitelist — bot responds to DMs from listed numbers
- **DM sender context:** `[WhatsApp DM from {sender_name}]` prefix so LLM knows who it's chatting with
- **Tool catalog full description:** `get_tool_catalog()` now includes full description (up to 300 chars) with shortcuts visible to planner
- **Test scenarios doc:** `senaryolar.md` — 10 delegation test scenarios with architecture overview
- **36 delegation tests:** Planner parse, delegate routing, processor execution, list/cancel tools, delegation log

### Changed

- **Planner prompt examples:** Weather scenarios use `web_fetch` with shortcuts instead of `web_search`
- **Agent processor channel injection:** Scheduler appends `IMPORTANT: set channel='{channel}'` to prompt for non-telegram channels
- **Function processor channel injection:** Scheduler injects `channel` into `tool_args` when missing
- **Agent delivery model:** Agent processor returns `(text, False)` — agent delivers via `send_message_to_user`, scheduler does NOT double-send
- **`_parse_tools()` no filter:** All tools including `send_message_to_user` pass through to LightAgent
- **`send_message_to_user` channel fallback:** Tries specified channel → whatsapp → telegram
- **Background task channel:** `SubagentWorker` passes `fallback_channel` to `create_background_task`
- **`CronJob` model:** Added `processor` and `plan_json` fields
- **`roles.yaml`:** Added `delegation` group for delegate/list/cancel tools

### Fixed

- **WhatsApp channel routing:** LightAgent now uses correct channel (was defaulting to telegram)
- **Double message bug:** Each processor type has exactly one delivery path — no duplicates
- **Tool catalog truncation:** Planner couldn't see `web_fetch` shortcuts (was showing only first line of description)

## [1.10.0] - 2026-02-21

### Added (WhatsApp Channel — WAHA Integration)

- **WAHA REST API client:** `WAHAClient` async client — `send_text()`, `get_session_status()`, phone↔chat_id conversion helpers
- **WhatsApp webhook handler:** `POST /webhooks/whatsapp/{user_id}` — full message processing pipeline (like Telegram)
- **Global webhook:** `POST /webhooks/whatsapp` — auto-routes by sender phone via `user_channels` table, only processes allowed groups
- **Allowed groups:** `allowed_groups` config list — bot only sees and responds to messages in specified groups
- **`[gbot]` response prefix:** All bot responses prefixed with `[gbot]` to distinguish from real messages
- **Loop prevention:** Bot's own `[gbot]` messages (fromMe) are skipped to prevent infinite loops
- **DM config flags:** `respond_to_dm` and `monitor_dm` — configurable DM behavior (both default `false`)
- **Duplicate event filtering:** `message.any` only used for `fromMe` messages, `message` for regular incoming (prevents double processing)
- **Non-chat filtering:** Newsletter (`@newsletter`), broadcast (`@broadcast`) messages ignored — only `@c.us` and `@g.us` accepted
- **Message splitting:** `split_message()` splits long responses at paragraph boundaries (WhatsApp 4096 char limit)
- **Scheduler integration:** `_send_to_channel()` supports WhatsApp — proactive messaging via WAHA for cron/reminders
- **WhatsApp send tool:** `send_whatsapp_message` in messaging tools — send messages to saved WhatsApp contacts
- **WAHA Docker service:** `docker-compose.yml` includes WAHA container with health check
- **35 tests:** WAHAClient helpers, message splitting, webhook handler (group/DM/filtering/session), global webhook routing

### Changed

- **`WhatsAppChannelConfig`:** Replaced Baileys fields (`bridge_url`) with WAHA fields (`waha_url`, `session`, `api_key`, `allowed_groups`, `respond_to_dm`, `monitor_dm`)
- **Session isolation:** WhatsApp messages stored in WhatsApp-specific sessions, never leak to Telegram/API sessions

## [1.9.0] - 2026-02-21

### Added (Web Tools & Multi-Provider)

- **Web search 4-provider fallback:** DuckDuckGo (free) → Tavily (free 1000/mo) → Moonshot $web_search → Brave Search API
- **DuckDuckGo search:** Primary free search provider, no API key needed, `asyncio.to_thread()` wrapper
- **Tavily search:** AI-optimized search as second fallback, free tier 1000 requests/month
- **Moonshot $web_search:** Kimi built-in web search as third fallback ($0.005/call)
- **`web_fetch` shortcut system:** Tag-based data access — `web_fetch("gold")` resolves to API URL from config
- **`fetch_shortcuts` in config.yaml:** Configurable shortcut → URL mapping, no hardcoded URLs in code
- **7 default shortcuts:** gold, currency, weather:istanbul, weather:ankara, weather:izmir, earthquake, news
- **`WebToolConfig.fetch_shortcuts`:** New config field (`dict[str, str]`) for deployment-specific shortcuts
- **Tool call debug logging:** `reason` node logs tool call names, `execute_tools` logs execution and results
- **`reasoning_content` preservation:** Thinking model output saved in `AIMessage.additional_kwargs`, restored in conversation history for tool call round-trips

### Changed

- **Model switched to MiniMax M2.5:** `openrouter/minimax/minimax-m2.5` — output 3x cheaper than Kimi K2.5 ($1.10 vs $3.00 per 1M tokens)
- **OpenRouter provider activated:** `OPENROUTER_API_KEY` in `.env`, provider config in `config.yaml`
- **`reasoning_effort` parameter:** Added to `litellm.achat()` for thinking models via OpenRouter
- **`web_fetch` docstring dynamic:** Tool description auto-generated from config shortcuts at startup
- **`.env` key renamed:** `OPEN_ROUTER_KEY` → `OPENROUTER_API_KEY` (LiteLLM convention)

### Removed

- **$web_search injection from litellm.py:** Moonshot-specific code moved to `_moonshot_search()` in web.py
- **`crypto` and `bist` shortcuts:** Removed (user preference + broken API)

## [1.8.0] - 2026-02-19

### Added (Tool Registry & Management)

- **ToolRegistry class:** Central tool registry — single source of truth for tool metadata, groups, and availability
- **ToolInfo dataclass:** Per-tool metadata (group, requires, available) for introspection
- **`register_group()`:** Factory functions register tools under named groups automatically
- **`register_unavailable()`:** Dynamic tools (scheduling, delegation) registered as known-but-unavailable when dependencies missing
- **`validate_roles()`:** Startup validation — detects unknown groups in roles.yaml, logs warnings
- **`GET /admin/tools`:** New admin endpoint for tool catalog introspection (names, groups, availability, dependencies)
- **2 new tests:** `test_registry_validate_roles`, `test_registry_groups_summary`

### Changed

- **`make_tools()` returns `ToolRegistry`** instead of `list[BaseTool]` — all consumers updated
- **`roles.yaml` simplified:** Tool names completely removed; only role → groups + context_layers + max_sessions remain. Tool-to-group mapping now comes from code (ToolRegistry)
- **`permissions.py`:** `get_allowed_tools()` accepts optional `registry` parameter — resolves tool names from registry groups instead of YAML
- **`GraphRunner`:** Uses ToolRegistry for RBAC resolution; accepts `list | ToolRegistry | None` for backward compatibility
- **`app.py` lifespan:** Startup validates roles.yaml groups against registry, logs warnings for unknown groups
- **`build_background_registry()`:** New function extracts background-safe subset from main ToolRegistry
- **Test updates:** `test_make_tools_all` → `test_make_tools_returns_registry` (dynamic assertions, no hardcoded counts)

### Improved

- **Adding a new tool now requires 1 file change** (was 5): add function to factory, it's auto-registered in the correct group
- **No more tool name sync between code and YAML** — ToolRegistry is the single source of truth

## [1.7.0] - 2026-02-19

### Added (Session Summarization & Fact Extraction)

- **`asummarize()`:** Hybrid format session summary (narrative paragraph + structured bullets: TOPICS/DECISIONS/PENDING/USER_INFO)
- **`aextract_facts()`:** Structured JSON extraction (preferences + notes) from conversation, saved to DB
- **`_rotate_session()` rewrite:** LLM-based summary + fact extraction + robust fallback on errors
- **3 preference tools:** `set_user_preference`, `get_user_preferences`, `remove_user_preference` in memory group
- **`remove_preference()`:** New MemoryStore method for preference deletion
- **Session summarization policy doc:** `docs/session_summarization.md`

### Fixed

- **Closed session reuse bug:** When a closed session_id is sent, a new session is created instead of reusing the dead one

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
