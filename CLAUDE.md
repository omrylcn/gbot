# CLAUDE.md — GraphBot Project Rules

## What is this?
LangGraph-based AI assistant framework. Combines nanobot (multi-channel agent) + ascibot (FastAPI+SQLite+RAG).

## DO / DON'T

**DO:**
- Communicate in Turkish with user, write code/docstrings in English
- Use `uv` for everything: `uv sync`, `uv run pytest`, `uv run ruff`
- Use `Config(BaseSettings)` from pydantic-settings (env_prefix="GRAPHBOT_", .env support)
- Use flat pytest functions (not class-based TestCase)
- Keep things simple — "basit ama katmanlı"
- Read `mimari_kararlar.md` for detailed architectural reasoning (11 decisions)
- Read `howtowork-development-plan.md` for phases 0–10 implementation details
- Read `development-plan2.md` for phases 11–23 detailed plan
- Check `todo.md` for current progress
- Do finish phase, change version and update `changelog.md` 

**DON'T:**
- Touch `reference files/` — read-only reference code (ascibot + nanobot)
- Use LangGraph checkpoint for persistence — SQLite is source of truth
- Use MessageBus — FastAPI handlers call GraphRunner directly
- Use nanobot's custom Tool ABC — use LangGraph native (@tool, BaseTool)
- Use markdown memory files — single SQLite layer
- Use `src/graphbot/` layout — flat `graphbot/` at repo root
- Over-engineer tests — minimum effort, cover CRUD

## Architecture (11 rules)

1. LangGraph = stateless executor (no checkpoint for data)
2. SQLite = source of truth (10 tables)
3. GraphRunner = orchestrator (SQLite ↔ LangGraph bridge, request-scoped)
4. FastAPI = main service (lifespan hosts background services)
5. Config = YAML + BaseSettings + .env
6. Tools = LangGraph native @tool / BaseTool
7. Sessions = token-based (30k limit, LLM summary on transition)
8. Graph = 4 nodes: load_context → reason ⇄ execute_tools → respond
9. ContextBuilder = 6 layers (identity, agent_memory, user_ctx, prev_summary, skills, skills_index)
10. Copy & adapt from reference code, never import as dependency
11. Write code docstrings as numpy style, but not too long. Always use English.

## Key Files

| File | What |
|------|------|
| `graphbot/core/config/schema.py` | Config(BaseSettings) + nested models |
| `graphbot/core/config/loader.py` | YAML loader → Config(**data) |
| `graphbot/memory/store.py` | MemoryStore — SQLite 10 tables, full CRUD |
| `graphbot/memory/models.py` | Item, ItemCard, ChatRequest/Response |
| `graphbot/agent/state.py` | AgentState(MessagesState) |
| `graphbot/agent/nodes.py` | Graph node functions |
| `graphbot/agent/graph.py` | StateGraph compile |
| `graphbot/agent/context.py` | ContextBuilder (SQLite-based) |
| `graphbot/agent/runner.py` | GraphRunner orchestrator |
| `graphbot/core/providers/litellm.py` | LiteLLM → AIMessage wrapper |
| `mimari_kararlar.md` | 11 architectural decisions (detailed reasoning) |
| `howtowork-development-plan.md` | Phases 0–10 implementation plan |
| `development-plan2.md` | Phases 11–23 detailed plan |
| `background_task_analiz.md` | Background task problem analysis & LightAgent solution (Faz 13 reference) |
| `todo.md` | Phase progress tracking |

## Progress

- [x] Faz 0: Skeleton
- [x] Faz 1: Config + MemoryStore (16 tests)
- [x] Faz 2–10: See todo.md (106 tests)

## SQLite Tables (10)
users, user_channels, sessions, messages, agent_memory, user_notes, activity_logs, favorites, preferences, cron_jobs

## Git & Release Strategy

- **Private repo** (`origin`) → tüm branch'ler (dev, feature/*)
- **Public repo** (`public`) → sadece `main` + tag'ler
- `dev`'de geliştir → hazır olunca `main`'e merge → `git push public main --tags`
- Version: `graphbot/__version__.py` tek kaynak (hatch dynamic)

## Commands
```bash
uv sync --extra dev && uv run pytest tests/ -v
```
