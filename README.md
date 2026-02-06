# GraphBot

LangGraph tabanlı, genel amacli AI asistan framework'u.

nanobot'un event-driven altyapisi + ascibot'un RAG/structured memory'si + LangGraph agent orchestration.

## Mimari

```
Giris Noktalari                    GraphRunner                LangGraph
(API, WebSocket, Webhook)    (orkestrator, req-scoped)    (stateless executor)
         |                            |                         |
         +---> runner.process() ---> SQLite'dan oku ---> graph.ainvoke(state)
                                     |                         |
                                     |                    load_context
                                     |                    reason (LLM)
                                     |                    execute_tools
                                     |                    respond
                                     |                         |
                                     +--- SQLite'a yaz <------+
```

- **LangGraph** = stateless executor (checkpoint kullanilmiyor)
- **SQLite** = source of truth (session, memory, user data)
- **GraphRunner** = orkestrator (SQLite <-> LangGraph koprusu)
- **FastAPI** = ana servis (REST, WebSocket, webhook, background tasks)

## Kurulum

```bash
uv sync
```

## Calistirma

```bash
# API server
make run

# veya
uv run uvicorn graphbot.api.app:app --reload --port 8000
```

## Proje Yapisi

```
graphbot/                   # Ana Python paketi
├── core/                   # Altyapi (config, providers, channels, cron, background)
├── agent/                  # LangGraph agent (state, nodes, graph, runner, tools, skills)
├── rag/                    # Semantic search (FAISS, Faz 9)
├── memory/                 # SQLite memory store
├── api/                    # FastAPI (REST, WebSocket, webhook)
└── cli/                    # Typer CLI

ascibot/                    # Referans kod (Turk mutfagi AI asistani)
nanobot/                    # Referans kod (multi-channel agent framework)
```

## Komutlar

```bash
make install    # uv sync
make test       # pytest
make lint       # ruff check
make format     # ruff format
make clean      # __pycache__ temizle
make run        # uvicorn server
```

## Dokumantasyon

- [mimari_kararlar.md](./mimari_kararlar.md) — 8 mimari karar (tartisma & gerekce)
- [howtowork-development-plan.md](./howtowork-development-plan.md) — implementasyon plani
- [todo.md](./todo.md) — ilerleme takibi

## Teknolojiler

| Bilesen | Teknoloji |
|---------|-----------|
| Agent | LangGraph StateGraph |
| LLM | LiteLLM (multi-provider) |
| API | FastAPI |
| Memory | SQLite |
| RAG | FAISS + sentence-transformers |
| Config | YAML + Pydantic |
| Background | APScheduler |
| CLI | Typer |
