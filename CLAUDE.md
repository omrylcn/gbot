# CLAUDE.md — GBot Project Rules

## What is this?
LangGraph-based AI assistant framework. Personal, observable, modular.

## DO / DON'T

**DO:**
- Communicate in Turkish with user, write code/docstrings in English
- Use `uv` for everything: `uv sync`, `uv run pytest`, `uv run ruff`
- Use `Config(BaseSettings)` from pydantic-settings (env_prefix="GBOT_", .env support)
- Use flat pytest functions (not class-based TestCase)
- Keep things simple — "basit ama katmanli"
- Read `notes/mimari_kararlar.md` for detailed architectural reasoning (16 decisions)
- Read `notes/development-plan2.md` for phases 17–28 detailed plan
- Check `notes/todo.md` for current progress and priority
- Do finish phase, change version and update `changelog.md`
- When `config/config.yaml` structure changes, sync `config/config.example.yaml` — replace secrets with `${ENV_VAR}` placeholders, keep comments

**DON'T:**
- Commit `config/config.yaml` — it contains secrets; only `config.example.yaml` goes to repo
- Touch `reference files/` — read-only reference code (ascibot + nanobot)
- Use LangGraph checkpoint for persistence — SQLite is source of truth
- Use MessageBus — FastAPI handlers call GraphRunner directly
- Use nanobot's custom Tool ABC — use LangGraph native (@tool, BaseTool)
- Use markdown memory files — single SQLite layer
- Use `src/gbot/` layout — flat `gbot/` + `gbot_cli/` at repo root
- Over-engineer tests — minimum effort, cover CRUD

## Architecture (16 decisions, 12 core rules)

1. LangGraph = stateless executor (no checkpoint for data)
2. SQLite = source of truth (15 tables)
3. GraphRunner = orchestrator (SQLite ↔ LangGraph bridge, request-scoped)
4. FastAPI = main service (lifespan hosts background services)
5. Config = YAML (config/) + BaseSettings + .env; secrets in .env, structure in config.example.yaml
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
| `gbot/` | Core framework — agent, API, memory, config, channels, background |
| `gbot_cli/` | CLI package — Typer commands, REPL, API client, Rich output |

CLI imports from `gbot` (config, store, runner, auth) but lives in its own package.
Entry point: `gbot`.

## Key Files

| File | What |
|------|------|
| `gbot/core/config/schema.py` | Config(BaseSettings) + nested models |
| `gbot/core/config/loader.py` | YAML loader → Config(**data) |
| `gbot/memory/store.py` | MemoryStore — SQLite 15 tables, full CRUD |
| `gbot/memory/models.py` | Item, ItemCard, ChatRequest/Response |
| `gbot/agent/state.py` | AgentState(MessagesState) |
| `gbot/agent/nodes.py` | Graph node functions |
| `gbot/agent/graph.py` | StateGraph compile |
| `gbot/agent/context.py` | ContextBuilder (8 layers, RBAC-aware) |
| `gbot/agent/runner.py` | GraphRunner orchestrator |
| `gbot/agent/permissions.py` | RBAC — roles.yaml loader, tool/context filtering |
| `gbot/agent/light.py` | LightAgent — isolated background agent |
| `gbot/agent/delegation.py` | DelegationPlanner — LLM-based subagent planning |
| `gbot/core/providers/litellm.py` | LiteLLM → AIMessage wrapper |
| `gbot/api/admin.py` | Admin API endpoints (owner-only) |
| `gbot_cli/commands.py` | Typer CLI (gbot run, chat, login, status, user, cron) |
| `gbot_cli/repl.py` | Interactive REPL — Rich banner, autocomplete, slash commands |
| `gbot_cli/client.py` | GraphBotClient — sync httpx API wrapper |
| `gbot_cli/slash_commands.py` | SlashCommandRouter — /help, /status, /session, ... |
| `gbot/agent/profiles.py` | Agent profiles — agents.yaml loader, AGENT.md/skills per agent type |
| `gbot/agent/tools/skill_tools.py` | load_skill tool — progressive disclosure |
| `config/agents.yaml` | Agent profiles (which AGENT.md, skills per agent type) |
| `config/roles.yaml` | RBAC role definitions (role → groups, no tool names — resolved from ToolRegistry) |
| `config/config.yaml` | Main configuration — gitignored, contains secrets |
| `config/config.example.yaml` | Config template — committed, secrets as `${ENV_VAR}` placeholders |
| `notes/important_notes.md` | Credentials, WAHA setup, WhatsApp architecture, memory design notes |
| `notes/mimari_kararlar.md` | 16 architectural decisions (detailed reasoning) |
| `notes/todo.md` | Phase progress tracking |
| `notes/rbac_mimari.md` | RBAC architecture (3 roles, tool groups, 2-layer guard) |
| `notes/session_summarization.md` | Session summarization policy (hybrid LLM + fact extraction) |
| `notes/development-plan2.md` | Phases 17–28 detailed plan |

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
- [x] Faz 19: AGENT.md & Skills — config/ dir, agents.yaml profiles, prompt extraction, load_skill tool, progressive disclosure (334 tests)
- [x] Faz 20: Context Service — context/ package, build_layers(), ContextService facade, 8 admin API endpoints, runtime overrides (348 tests)

## SQLite Tables (15)
users, user_channels, sessions, messages, agent_memory, user_notes, activity_logs, favorites, preferences, cron_jobs, cron_execution_log, reminders, system_events, background_tasks, api_keys

## Git & Release Strategy

- **Private repo** (`origin`) → all branches (dev, feature/*)
- **Public repo** (`public`) → only `main` + tags
- Develop on `dev` → merge to `main` → `git push public main --tags`
- Version: `gbot/__version__.py` single source (hatch dynamic)

## Commands
```bash
uv sync --extra dev && uv run pytest tests/ -v
uv run ruff check gbot/ gbot_cli/
```
