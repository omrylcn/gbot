# CLAUDE.md — GraphBot Project Rules

## What is this?
LangGraph-based AI assistant framework. Combines nanobot (multi-channel agent) + ascibot (FastAPI+SQLite+RAG).

## DO / DON'T

**DO:**
- Communicate in Turkish with user, write code/docstrings in English
- Use `uv` for everything: `uv sync`, `uv run pytest`, `uv run ruff`
- Use `Config(BaseSettings)` from pydantic-settings (env_prefix="GRAPHBOT_", .env support)
- Use flat pytest functions (not class-based TestCase)
- Keep things simple — "basit ama katmanli"
- Read `mimari_kararlar.md` for detailed architectural reasoning (12 decisions)
- Read `howtowork-development-plan.md` for phases 0–10 implementation details
- Read `development-plan2.md` for phases 17–28 detailed plan
- Read `future_works.md` for strategic directions
- Check `todo.md` for current progress and priority
- Do finish phase, change version and update `changelog.md`

**DON'T:**
- Touch `reference files/` — read-only reference code (ascibot + nanobot)
- Use LangGraph checkpoint for persistence — SQLite is source of truth
- Use MessageBus — FastAPI handlers call GraphRunner directly
- Use nanobot's custom Tool ABC — use LangGraph native (@tool, BaseTool)
- Use markdown memory files — single SQLite layer
- Use `src/graphbot/` layout — flat `graphbot/` + `gbot_cli/` at repo root
- Over-engineer tests — minimum effort, cover CRUD

## Architecture (12 rules)

1. LangGraph = stateless executor (no checkpoint for data)
2. SQLite = source of truth (15 tables)
3. GraphRunner = orchestrator (SQLite ↔ LangGraph bridge, request-scoped)
4. FastAPI = main service (lifespan hosts background services)
5. Config = YAML + BaseSettings + .env
6. Tools = LangGraph native @tool / BaseTool
7. Sessions = token-based (30k limit, LLM summary on transition)
8. Graph = 4 nodes: load_context → reason ⇄ execute_tools → respond
9. ContextBuilder = 8 layers (identity, runtime, role, agent_memory, user_ctx, events, session_summary, skills)
10. Copy & adapt from reference code, never import as dependency
11. Write code docstrings as numpy style, but not too long. Always use English.
12. RBAC = 3 roles (owner/member/guest), roles.yaml, 2-layer guard (reason filter + execute guard)

## Two Packages

| Package | Role |
|---------|------|
| `graphbot/` | Core framework — agent, API, memory, config, channels, background |
| `gbot_cli/` | CLI package — Typer commands, REPL, API client, Rich output |

CLI imports from `graphbot` (config, store, runner, auth) but lives in its own package.
Entry point: `gbot` (alias: `graphbot`).

## Key Files

| File | What |
|------|------|
| `graphbot/core/config/schema.py` | Config(BaseSettings) + nested models |
| `graphbot/core/config/loader.py` | YAML loader → Config(**data) |
| `graphbot/memory/store.py` | MemoryStore — SQLite 15 tables, full CRUD |
| `graphbot/memory/models.py` | Item, ItemCard, ChatRequest/Response |
| `graphbot/agent/state.py` | AgentState(MessagesState) |
| `graphbot/agent/nodes.py` | Graph node functions |
| `graphbot/agent/graph.py` | StateGraph compile |
| `graphbot/agent/context.py` | ContextBuilder (8 layers, RBAC-aware) |
| `graphbot/agent/runner.py` | GraphRunner orchestrator |
| `graphbot/agent/permissions.py` | RBAC — roles.yaml loader, tool/context filtering |
| `graphbot/agent/light.py` | LightAgent — isolated background agent |
| `graphbot/agent/delegation.py` | DelegationPlanner — LLM-based subagent planning |
| `graphbot/core/providers/litellm.py` | LiteLLM → AIMessage wrapper |
| `graphbot/api/admin.py` | Admin API endpoints (owner-only) |
| `gbot_cli/commands.py` | Typer CLI (gbot run, chat, login, status, user, cron) |
| `gbot_cli/repl.py` | Interactive REPL — Rich banner, autocomplete, slash commands |
| `gbot_cli/client.py` | GraphBotClient — sync httpx API wrapper |
| `gbot_cli/slash_commands.py` | SlashCommandRouter — /help, /status, /session, ... |
| `roles.yaml` | RBAC role definitions (role → groups, no tool names — resolved from ToolRegistry) |
| `mimari_kararlar.md` | 13 architectural decisions (detailed reasoning) |
| `notes.md` | Kullanıcı şifreleri, WAHA kurulum adımları, WhatsApp credentials, memory tasarım notları |
| `todo.md` | Phase progress tracking |

## Progress

- [x] Faz 0–10: Core framework (106 tests)
- [x] Faz 11: Auth & API Security (134 tests)
- [x] Faz 12: Agent Prompting & Context (143 tests)
- [x] Faz 13–13.6: LightAgent, Background, WS Events, Delegation (230 tests)
- [x] Faz 15: Docker & Deploy
- [x] Faz 16: CLI Enhancement — Rich REPL, slash commands, admin API (253 tests)
- [x] Faz 16.5: RBAC — 3 roles, roles.yaml, 2-layer guard (264 tests)
- [x] Faz 17: Session Summarization — hybrid LLM summary, fact extraction, preference tools (281 tests)
- [x] Faz 18: Tool Registry — ToolRegistry class, auto group mapping, roles.yaml simplified, /admin/tools (283 tests)

## SQLite Tables (15)
users, user_channels, sessions, messages, agent_memory, user_notes, activity_logs, favorites, preferences, cron_jobs, cron_execution_log, reminders, system_events, background_tasks, api_keys

## Git & Release Strategy

- **Private repo** (`origin`) → all branches (dev, feature/*)
- **Public repo** (`public`) → only `main` + tags
- Develop on `dev` → merge to `main` → `git push public main --tags`
- Version: `graphbot/__version__.py` single source (hatch dynamic)

## Commands
```bash
uv sync --extra dev && uv run pytest tests/ -v
uv run ruff check graphbot/ gbot_cli/
```
