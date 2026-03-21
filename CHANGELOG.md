# Changelog

All notable changes to this project will be documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


## [1.18.0] - 2026-03-21 — Faz 22C: Decay, Relations, Memory Tools

### Added

- **2-stage retrieval:** ContextBuilder searches 20 candidates via sqlite-vec → re-ranks by `similarity × retrieval_strength` (recency × access_count × confidence) → top 10 enter context.
- **Access tracking:** `batch_increment_access()` — facts entering context get access_count +1. Frequently accessed facts score higher in re-ranking.
- **Decay logic:** `apply_decay()` in store.py — 30+ day old facts with 0 access lose importance, <0.1 importance → archived. Available but not auto-triggered (Faz D).
- **`memory_relations` table:** Entity relationships extracted from conversations. `add_relation()`, `get_relations()`, `invalidate_relation()`.
- **Relation extraction:** AGENT.MD updated — relations zorunlu, 8 relation types (works_at, works_with, lives_in, owns, married_to, knows, uses, studies).
- **`MemoryConsolidator`:** Event-driven consolidation class (merge + decay). Fact merge disabled pending threshold tuning (Faz D).
- **Memory tools:** `search_memory` (semantic search), `forget_fact` (invalidate by query), `what_do_you_know` (category-grouped list). 26 total tools.

### Changed

- **AGENT.MD fully Turkish:** 10 zorunlu category, relation examples, merge prompt, AUDN Türkçe.
- **Extraction model:** Back to Gemini Flash (better category/relation quality than gpt-4o-mini).
- **`_extract_typed_facts()` returns tuple:** `(facts, relations)` — relations parsed and saved alongside facts.
- **`make_memory_tools()`:** Accepts `embedder` parameter for search_memory/forget_fact.
- **`make_tools()`:** Accepts `embedder` parameter, passed through to memory tools.


## [1.17.0] - 2026-03-21 — Faz 22B: Semantic Retrieval + AUDN

### Added

- **sqlite-vec integration:** Vector search in SQLite — `vec_memory_facts` virtual table with cosine distance.
- **`MemoryEmbedder`:** Sync OpenRouter embedding client (`google/gemini-embedding-001`, 3072d). Config: `memory.embedding`.
- **AUDN update logic:** Embedding finds similar facts → LLM decides ADD/UPDATE/DELETE/NOOP. Config: `memory.update` (strategy, model).
- **DELETE action:** "Artık X yapmıyorum" → old fact invalidated, no negative fact added.
- **Query-aware context:** ContextBuilder embeds last user message → semantic search → most relevant facts in context.
- **Temporal user_notes:** Notes processed → transferred to memory_facts via AUDN → always deleted after processing.
- **`MemoryEmbeddingConfig` + `MemoryUpdateConfig`:** Full config for embedding provider/model and update strategy/thresholds.
- **Embedding benchmark notebook:** `notebooks/embedding_benchmark.ipynb` — 21 models tested for Turkish semantic similarity.

### Changed

- **`memory.update.strategy` default:** `llm` (was `cascading`). Embedding finds, LLM decides — most accurate.
- **`MemoryService`:** Now accepts `config` and `embedder` parameters. AUDN replaces exact string dedup.
- **`ContextBuilder`:** Accepts `embedder` parameter, `build_layers()` takes `last_message` for semantic retrieval.
- **`store.add_fact()`:** New `embedding` parameter — stores vector in same transaction.
- **`store.invalidate_fact()`:** Also removes vector from `vec_memory_facts`.
- **`store.search_similar_facts()`:** CTE + `k=` syntax for sqlite-vec KNN with user/validity filtering.


## [1.16.0] - 2026-03-20 — Faz 22A: Memory Layer

### Added

- **`memory_facts` table:** Typed fact storage with confidence, importance, category, keywords. 4 fact types: semantic, episodic, preference, procedural.
- **`memory_processing_log` table:** Audit trail for extraction runs (trigger, counts, duration).
- **`MemoryService`:** Unified memory processing — session summarization + fact extraction via `agents.yaml` memory profile.
- **Memory agent profile:** `config/agents.yaml` memory entry + `workspace/agents/memory/AGENT.md` prompt.
- **Hot-path extraction:** Every N user messages (configurable `background.memory.extraction_every_n`, default 5), fire-and-forget async extraction.
- **`MemoryConfig`:** Root-level `memory` config section — enabled, model, extraction_every_n, max_facts_per_user, embedding (provider/model/dimension), update (strategy/model/thresholds).
- **ContextBuilder:** `user_context` layer now includes "LEARNED FACTS" from `memory_facts` alongside explicit notes/prefs/favs.
- **Admin API:** `GET /admin/memory/{user_id}` — facts, stats, processing log. Stats include `memory_facts` count.
- **Dashboard Memory page:** Facts table with type filter tabs, notes/preferences/favorites panel, processing log.
- **Embedding config:** `memory.embedding` — provider (openrouter/local), model (`google/gemini-embedding-001`), dimension (3072). Benchmarked 21 models for Turkish semantic similarity.
- **Update strategy config:** `memory.update` — cascading (default), llm_only, threshold_only. Thresholds tuned per embedding benchmark (noop >0.90, add <0.65).

### Changed

- **Unified `user_notes` table:** `user_notes` + `preferences` + `favorites` → single `user_notes` table with `note_type` column (note/preference/favorite). All legacy method signatures preserved.
- **Runner `_rotate_session()`:** Delegates to `MemoryService.process_session()` via fire-and-forget `asyncio.create_task`. Runner no longer imports `asummarize`/`aextract_facts` directly.
- **Extraction decoupled from `litellm_llm.py`:** MemoryService uses `achat` with memory agent profile, not hardcoded prompts.

### Removed

- **`_save_extracted_facts()` in runner.py** — Logic moved to `MemoryService.extract_and_save()`.
- **Backward compat writes:** MemoryService no longer duplicates facts into `user_notes` — `memory_facts` and `user_notes` are separate layers.


## [1.15.0] - 2026-03-20 — Faz 21: Unified BackgroundTask

### Changed

- **Unified task tables:** 5 scheduling tables (`cron_jobs`, `reminders`, `background_tasks`, `cron_execution_log`, `delegation_log`) merged into 2 tables: `background_tasks` (unified) + `task_executions` (audit log). Auto-migration preserves all existing data.
- **`background_tasks` schema:** `execution_type` (immediate/delayed/recurring/monitor) + `processor` (static/function/agent/runner) columns replace implicit table-based classification.
- **`BackgroundTask` model:** Replaces `CronJob` in `gbot/core/cron/types.py`. `CronJob` kept as alias for backward compatibility. Accepts `job_id` and `reminder_id` as aliases for `task_id`.
- **Unified `_execute_task()`:** `CronScheduler._execute_job()` and `_execute_reminder()` merged into single `_execute_task()` method.
- **Proactive messages as `role="system"`:** Background task results recorded to session as `SystemMessage` — agent sees delivery info but doesn't repeat content.
- **API:** `/admin/crons` → `/admin/tasks` with `execution_type` and `status` query filters. Stats response: `cron_jobs` → `recurring_tasks`, `reminders` → `pending_delayed`.
- **Dashboard Tasks page:** Renamed from "Crons", shows all task types with Active/All/Completed filter tabs.
- **SKILL.md (scheduling):** Simplified — all scheduling through `delegate` tool, no more `create_reminder`/`add_cron_job` references.

### Removed

- **`gbot/agent/tools/cron_tool.py`** — `add_cron_job`, `list_cron_jobs`, `remove_cron_job`, `create_alert` tools deleted. `delegate` handles all scheduling.
- **`gbot/agent/tools/reminder.py`** — `create_reminder`, `list_reminders`, `cancel_reminder` tools deleted.
- **`activity_logs` table** — Never implemented, references removed from docs and CLAUDE.md.
- **ContextBuilder `events` layer** — Removed (duplicate with session history recording).

### Fixed

- **Proactive message duplication:** Agent no longer repeats background task results as its own message. System role prevents confusion.
- **Function processor recording:** `_record_proactive_message` now called for all processor types including `function` (previously skipped when `response=None`).

## [1.14.1] - 2026-03-19

### Improved

- **CronScheduler proactive message recording:** Cron jobs and reminders now record their responses to the user's active session via `_record_proactive_message()` helper.

## [1.14.0] - 2026-03-14

### Added (Faz 20: Context Service, Admin Dashboard & API)

- **`gbot/agent/context/` package:** Restructured from single file to package — `models.py`, `builder.py`, `service.py` with backward-compatible `__init__.py` re-exports
- **`LayerResult` Pydantic model:** Per-layer inspection with name, description, source, content, chars, tokens, budget, truncated, enabled fields
- **`ContextOverride` Pydantic model:** Runtime layer override definition (content + enabled)
- **`ContextBuilder.build_layers()`:** Returns `dict[str, LayerResult]` — layer-by-layer breakdown with content. `mark_delivered` parameter controls event side-effects. `template_vars` parameter for planner identity injection
- **`_LAYER_META` dict:** Description and source metadata for each layer — full traceability in dashboard
- **Empty layer support:** Layers with no content (role, agent_memory, events) still included in `build_layers()` output for dashboard visibility
- **`ContextService`:** Unified facade for context inspection across all 3 agent types (main, planner, light) with runtime override support. Accepts optional `registry` for tool definition token calculation
- **`get_profile_context_layers()`:** Profile-aware layer filtering — reads `context_layers` from agents.yaml (`["*"]` = all, `[identity]` = minimal)
- **Informational layers:** `message_history` (session messages) and `tool_definitions` (ToolRegistry OpenAI schemas) added to dashboard for complete LLM input visibility
- **Planner/light context via layers:** `get_planner_context()` and `get_light_context()` return `dict[str, LayerResult]` for unified dashboard display
- **8 new admin API endpoints:**
  - `GET /admin/context/{profile}/layers` — layer-by-layer content + stats (all 3 profiles)
  - `GET /admin/context/{profile}/preview` — full rendered context string
  - `GET /admin/context/budget` — token budget breakdown
  - `GET /admin/context/overrides` — list all active overrides
  - `POST /admin/context/overrides` — set runtime layer overrides
  - `DELETE /admin/context/overrides/{layer}` — clear layer override
  - `GET /admin/context/profiles` — list agent profiles
  - `GET /admin/context/profiles/{name}` — profile detail + AGENT.md content
- **Admin Dashboard (React):** Separate web UI for monitoring and inspection
  - React 19 + TanStack Query + Zustand + Tailwind CSS 4 + Lucide icons + Vite
  - Pages: Dashboard, Context (layer inspector), Conversations (session browser + message history), Users, Tools, Crons, Settings
  - Context page: per-profile layer view with description, source, content, token/char stats
  - Conversations page: owner can view all users' sessions and message history
  - Dark/light theme with system preference detection
  - JWT auth integration (login page with GBot branding)
  - Deployed as separate Docker container (nginx:alpine), proxies API via `/api/` rewrite
- **`dashboard/` directory:** Complete React project — Dockerfile, nginx.conf, API client, stores, components
- **GBot logo:** SVG logo (`gbot_logo.svg`) + PNG (`logo.png`) + favicon (`dashboard/public/favicon.png`)
- **Owner session bypass:** Owner can view any user's sessions and message history (not just their own)
- **`/admin/users` role field:** User list now includes RBAC role from database
- **14 context service tests:** build_layers, side-effects, overrides, service methods, planner/light layer format

### Changed

- **`ContextBuilder.build()`:** Now delegates to `build_layers()` — same signature, same output
- **`ContextBuilder.get_context_stats()`:** Delegates to `build_layers()`, removes ~70 lines of duplicate logic
- **Skills index split:** `skills` and `skills_index` now separate LayerResult entries for finer inspection
- **`docker-compose.yml`:** Added dashboard service (gbot-dashboard, port 3001, depends on graphbot)
- **`app.py`:** ContextService now receives `registry` parameter for tool token calculation
- **`routes.py`:** Owner bypass — `current_user != config.owner_user_id` check for session/history access
- **README.md:** Complete rewrite — GBot branding, logo, dashboard section, context service docs, agent profiles, updated project structure

## [1.13.0] - 2026-03-14

### Added (Faz 19: AGENT.md & Skills Gözden Geçirme)

- **`config/` directory:** All YAML configs consolidated — `config.yaml`, `roles.yaml`, `agents.yaml` in single directory with backward-compatible fallback to root
- **`config/agents.yaml`:** Agent profile system — each agent type (main, planner, light) defines which AGENT.md and skills to use
- **`gbot/agent/profiles.py`:** AgentProfile loader with global cache, `get_agent_md()`, `get_agent_skills()`, `get_template_vars()` (same pattern as permissions.py)
- **`workspace/agents/planner/AGENT.md`:** Planner prompt extracted from Python to Markdown — template vars `{tool_catalog}` and `{extra_examples}` preserved
- **`workspace/agents/light/AGENT.md`:** Base context for LightAgent — identity, language rules, background task guidelines
- **`gbot/agent/skills/builtin/scheduling/SKILL.md`:** Scheduling decision tree extracted from main AGENT.md — available via progressive disclosure
- **`load_skill` tool:** Progressive disclosure — agent loads full skill instructions on demand instead of always-in-context
- **`skills` tool group:** Added to ToolRegistry and roles.yaml (owner + member)
- **11 profile tests:** Loading, fallback, cache, AGENT.md resolution, skill filtering

### Changed

- **`ContextBuilder`:** Profile-aware — accepts `profile` parameter, identity resolves from profile AGENT.md, skills filtered by profile config
- **`ContextBuilder._get_identity()`:** 5-level priority chain: prompt_template > system_prompt > profile AGENT.md > workspace/AGENT.md > persona config
- **`DelegationPlanner`:** Loads prompt from profile AGENT.md, falls back to `_PLANNER_PROMPT` constant
- **`LightAgent`:** Prepends base context from profile before task prompt
- **`workspace/AGENT.md`:** Scheduling section replaced with `load_skill("scheduling")` reference (83 → 41 lines, ~50% smaller)
- **Skills index instruction:** `read_file` → `load_skill(skill_name)` for skill loading
- **`docker-compose.yml`:** Single `./config:/app/config:ro` volume mount replaces two separate mounts
- **`load_config()`:** Resolution order now checks `config/config.yaml` before `config.yaml`
- **`_load_roles_yaml()`:** Resolution order now checks `config/roles.yaml` before `roles.yaml`

## [1.12.0] - 2026-02-28

### Added (LLM Provider Refactor — Strategy Pattern)

- **`BaseLLMProvider` ABC:** Abstract base class for LLM providers — `achat()` interface
- **`OpenRouterLLM` provider:** Direct OpenRouter SDK integration — bypasses LiteLLM adapter, `response_format` passes through without stripping
- **`LiteLLMLLM` provider:** Extracted existing LiteLLM logic into standalone class (Moonshot thinking, summarize, extract_facts)
- **`openrouter` SDK dependency:** `openrouter>=0.7.0` in pyproject.toml
- **13 provider tests:** Factory routing, AIMessage conversion, reasoning normalization, tool call parsing, facade delegation

### Changed

- **`litellm.py` → facade module:** Global provider instances (`_main_provider`, `_fallback_provider`), routes `openrouter/*` models to OpenRouterLLM, others to LiteLLMLLM — zero caller changes
- **`setup_provider()` routing:** Init-time provider selection based on model prefix, `os.environ` fallback for OpenRouter API key
- **`_RESPONSE_SCHEMA` nullable fields:** `type: ["string", "null"]` → `anyOf` syntax for OpenRouter JSON schema compatibility
- **Channel injection:** Always overrides with `state["channel"]` — LLM can no longer set wrong channel (was causing reminders to go to `api` instead of `telegram`)
- **`delegate` tool return message:** Includes execution details (cron expr, delay, one-shot) so LLM reports accurately instead of inventing details

### Fixed

- **`response_format` stripping:** LiteLLM adapter was silently dropping `response_format` for OpenRouter models → DelegationPlanner ~40% empty JSON. Direct SDK fixes this.
- **`max_tokens=512` truncation:** Planner JSON responses were being cut off, causing parse failures and silent fallback to `immediate/agent`. Removed hard limit.
- **Channel delivery bug:** LLM was setting `channel: 'api'` for Telegram/WhatsApp requests — reminders delivered to wrong channel. Now always injected from state.
- **Planner error logging:** Added exception details and raw text (first 300 chars) to parse failure warnings
- **Misleading delegation confirmation:** When planner fell back to defaults, LLM still told user "setup complete" with fabricated details. Delegate tool now returns actual plan details.

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
- **`gbot_cli/` package:** CLI code moved from `gbot/cli/` to a separate `gbot_cli/` package
- **GraphBotClient:** Sync httpx wrapper for all API endpoints (health, chat, login, sessions, user, admin)
- **Credentials:** Token storage at `~/.gbot/credentials.json` with `chmod 0600`
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
- **`gbot/cli/` removed:** All CLI code moved to `gbot_cli/` package, old directory deleted

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
