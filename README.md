# GraphBot

LangGraph tabanli, genisletilebilir AI asistan framework'u.

nanobot'un multi-channel altyapisi + ascibot'un structured memory'si + LangGraph agent orchestration.

## Mimari

![architecture](images/architecture.png)


**Temel prensipler:**
- **LangGraph** = stateless executor — checkpoint kullanilmiyor
- **SQLite** = source of truth — session, memory, user data (10 tablo)
- **GraphRunner** = orkestrator — SQLite ↔ LangGraph koprusu (request-scoped)
- **LiteLLM** = multi-provider LLM — OpenAI, Anthropic, DeepSeek, Groq, Gemini, OpenRouter

## Ozellikler

| Ozellik | Aciklama |
|---------|----------|
| Multi-provider LLM | LiteLLM ile 6+ provider destegi |
| Coklu kanal | Telegram (aktif), Discord/WhatsApp/Feishu (stub) |
| Kullanici yonetimi | Owner-based erisim, CLI ile user/channel yonetimi |
| Uzun sureli hafiza | SQLite: notlar, tercihler, favoriler, aktiviteler |
| Session yonetimi | Token-limit bazli, otomatik ozet ile gecis |
| 9 tool grubu | Hafiza, dosya, shell, web, RAG, cron, reminder, delegate |
| Skill sistemi | Markdown-based, workspace override, always-on destegi |
| Zamanlanmis gorevler | Cron + one-shot reminder + heartbeat |
| Background worker | Async subagent spawn (delegate tool) |
| RAG | FAISS + sentence-transformers (opsiyonel) |
| WebSocket | Gercek zamanli chat |
| CLI | Typer-based: chat, status, user, cron yonetimi |

## Hizli Baslangic

### 1. Kurulum

```bash
uv sync --extra dev                    # gelistirme
uv sync --extra dev --extra rag        # + RAG (FAISS, sentence-transformers)
```

### 2. Konfigürasyon

`config.yaml`:
```yaml
assistant:
  name: "GraphBot"
  owner:
    username: "ali"
    name: "Ali"
  model: "openai/gpt-4o-mini"      # veya anthropic/claude-sonnet-4-5-20250929

providers:
  openai:
    api_key: "sk-..."              # veya .env'den
```

Veya `.env` dosyasi:
```
GRAPHBOT_PROVIDERS__OPENAI__API_KEY=sk-...
```

### 3. Baslatma

```bash
graphbot run                          # API server (varsayilan :8000)
graphbot run --port 3000 --reload     # farkli port + auto-reload
```

### 4. Kullanim

```bash
# CLI
graphbot chat                         # interaktif mod
graphbot chat -m "merhaba"            # tek mesaj

# REST API
curl http://localhost:8000/health
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "merhaba"}'

# WebSocket
wscat -c ws://localhost:8000/ws/chat
```

### 5. Telegram Bot

Her kullanici kendi Telegram bot'unu olusturur, token `user_channels` tablosunda saklanir.

```bash
# 1. @BotFather'dan bot olustur ve token al

# 2. config.yaml'da telegram'i aktif et:
#      channels:
#        telegram:
#          enabled: true

# 3. Kullanici ekle ve bot token'i bagla
graphbot user add ali --name "Ali" --telegram "123456:ABC_TOKEN"

# 4. Public URL olustur
ngrok http 8000

# 5. Webhook kaydet (path'te user_id var)
curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://xxxx.ngrok-free.app/webhooks/telegram/ali"

# 6. Server'i baslat ve Telegram'dan yaz
graphbot run
```

## Proje Yapisi

```
graphbot/
├── agent/
│   ├── context.py              # ContextBuilder (6 katman system prompt)
│   ├── graph.py                # StateGraph compile
│   ├── nodes.py                # 4 node: load_context, reason, execute_tools, respond
│   ├── runner.py               # GraphRunner orkestrator
│   ├── state.py                # AgentState(MessagesState)
│   ├── skills/
│   │   ├── loader.py           # SkillLoader (builtin + workspace)
│   │   └── builtin/            # summarize, weather
│   └── tools/
│       ├── memory_tools.py     # save_note, get_context, favorites, activities
│       ├── filesystem.py       # read/write/edit/list_dir
│       ├── shell.py            # exec_command (guvenlik filtreleri ile)
│       ├── web.py              # web_search (Brave), web_fetch
│       ├── search.py           # RAG: search_items, get_item_detail
│       ├── cron_tool.py        # add/list/remove cron jobs
│       ├── reminder.py         # create/list/cancel reminder
│       └── delegate.py         # spawn background subagent
├── api/
│   ├── app.py                  # create_app(), lifespan (DB, scheduler, worker)
│   ├── routes.py               # /chat, /health, /sessions, /user/context
│   ├── auth.py                 # /auth/register, /auth/login
│   ├── ws.py                   # WebSocket /ws/chat
│   └── deps.py                 # FastAPI dependency injection
├── cli/
│   └── commands.py             # Typer CLI (run, chat, status, cron, user)
├── core/
│   ├── config/
│   │   ├── schema.py           # Config(BaseSettings) + nested models
│   │   └── loader.py           # YAML → Config
│   ├── providers/
│   │   └── litellm.py          # LiteLLM → AIMessage wrapper
│   ├── channels/
│   │   ├── base.py             # resolve_user, allowlist, owner mode
│   │   └── telegram.py         # Webhook handler + send_message + md→html
│   ├── cron/
│   │   ├── scheduler.py        # APScheduler cron + one-shot
│   │   └── types.py            # CronJob model
│   └── background/
│       ├── heartbeat.py        # Periyodik wake-up servisi
│       └── worker.py           # Async subagent worker
├── memory/
│   ├── store.py                # MemoryStore — SQLite 10 tablo, 42+ metod
│   └── models.py               # Pydantic: ChatRequest, ChatResponse, Item, ...
└── rag/
    ├── retriever.py            # FAISS search + format
    └── indexer.py              # JSON → FAISS index
```

## CLI Komutlari

```bash
graphbot --version                    # Versiyon

graphbot run [--port 8000] [--reload] # API server
graphbot chat [-m "mesaj"] [-s sid]   # Terminal chat
graphbot status                       # Sistem durumu

graphbot user add ali --name "Ali" --telegram "TOKEN"  # Kullanici ekle
graphbot user list                    # Tum kullanicilar
graphbot user remove ali              # Kullanici sil
graphbot user link ali telegram 555   # Kanal bagla

graphbot cron list                    # Zamanlanmis gorevler
graphbot cron remove <job_id>         # Gorev sil
```

## API Endpoints

| Metod | Yol | Aciklama |
|-------|-----|----------|
| POST | `/chat` | Mesaj gonder, yanit al |
| GET | `/health` | Health check |
| GET | `/sessions/{user_id}` | Kullanicinin session'lari |
| GET | `/session/{sid}/history` | Session mesaj gecmisi |
| POST | `/session/{sid}/end` | Session'i kapat |
| GET | `/user/{user_id}/context` | Kullanici context'i |
| POST | `/auth/register` | Kullanici kayit |
| POST | `/auth/login` | Giris |
| GET | `/auth/user/{user_id}` | Profil |
| WS | `/ws/chat` | WebSocket chat |
| POST | `/webhooks/telegram/{user_id}` | Telegram webhook |

## Kullanici Yonetimi

GraphBot **owner-based** erisim kontrolu kullanir:

```yaml
# config.yaml
assistant:
  owner:
    username: "ali"     # Varsayilan kullanici
    name: "Ali"
```

- **Owner** tanimlanirsa: sadece DB'de kayitli kullanicilar erisebilir (owner startup'ta otomatik olusturulur)
- **Owner** tanimlanmazsa: legacy mod — `allow_from` listesi + otomatik kullanici olusturma

Kullanici ekleme:
```bash
graphbot user add veli --name "Veli" --telegram "BOT_TOKEN"
```

## Tool Sistemi

9 tool grubu, `config.yaml`'da `tools: ["*"]` ile hepsi aktif:

| Grup | Toollar | Aciklama |
|------|---------|----------|
| Memory | save_user_note, get_user_context, log_activity, get_recent_activities, add/get/remove_favorite | Kullanici hafizasi |
| Filesystem | read_file, write_file, edit_file, list_dir | Workspace dosya islemleri |
| Shell | exec_command | Guvenli shell (rm -rf, format vb. engelli) |
| Web | web_search, web_fetch | Brave Search + sayfa cekme |
| RAG | search_items, get_item_detail | Semantik arama (FAISS) |
| Cron | add/list/remove_cron_job | Tekrarlanan gorevler |
| Reminder | create/list/cancel_reminder | Tek seferlik hatirlatma |
| Delegate | delegate | Background subagent spawn |

## Skill Sistemi

Markdown-based, genisletilebilir beceri sistemi:

```
workspace/skills/         # Kullanici skill'leri (oncelikli)
graphbot/agent/skills/builtin/  # Yerlesik skill'ler
```

Her skill bir `SKILL.md` dosyasi:
```markdown
---
name: weather
description: Hava durumu sorgula
always: false
metadata:
  requires:
    bins: [curl]
---
# Weather Skill
...talimatlar...
```

- `always: true` → her zaman system prompt'a dahil
- Workspace skill'leri builtin'leri override eder
- `requirements` kontrolu: gerekli binary/env var eksikse skill devre disi

## Background Servisler

| Servis | Aciklama | Config |
|--------|----------|--------|
| CronScheduler | APScheduler ile cron + date trigger'lar | `background.cron.enabled` |
| Reminder | Tek seferlik geciktirilmis mesajlar | CronScheduler uzerinden |
| Heartbeat | Periyodik uyandirma, HEARTBEAT.md okur | `background.heartbeat.enabled/interval_s` |
| SubagentWorker | Async background task spawn | Otomatik |

## Konfigürasyon

Oncelik sirasi: `.env` > ortam degiskenleri > `config.yaml` > varsayilanlar

```bash
# .env (GRAPHBOT_ prefix, __ ayirici)
GRAPHBOT_ASSISTANT__MODEL=openai/gpt-4o-mini
GRAPHBOT_PROVIDERS__OPENAI__API_KEY=sk-...
GRAPHBOT_BACKGROUND__CRON__ENABLED=true
```

Tam config yapisi icin: [`config.yaml`](./config.yaml)

## Workspace

`workspace/` dizini bot'un calisma alani:

```
workspace/
├── AGENT.md              # Bot kimligi (system prompt)
├── HEARTBEAT.md          # Heartbeat talimatlari (opsiyonel)
└── skills/               # Kullanici skill'leri (opsiyonel)
```

`AGENT.md` bot'un kimligini tanimlar — system prompt olarak kullanilir.
`config.yaml`'daki `system_prompt` daha yuksek onceliklidir.

## RAG (Opsiyonel)

```yaml
# config.yaml
rag:
  embedding_model: "intfloat/multilingual-e5-small"
  data_source: "./data/items.json"
  index_path: "./data/faiss_index"
  text_template: "{title}. {description}. Kategori: {category}."
  id_field: "id"
```

```bash
uv sync --extra rag   # FAISS + sentence-transformers
```

Aktif edildiginde `search_items` ve `get_item_detail` tool'lari otomatik eklenir.

## SQLite Tablolari (10)

| Tablo | Amac |
|-------|------|
| `users` | Kullanici kayitlari |
| `user_channels` | Kanal baglantilari (telegram, discord, ...) |
| `sessions` | Konusma session'lari (token takibi) |
| `messages` | Chat mesajlari |
| `agent_memory` | Key-value uzun sureli hafiza |
| `user_notes` | Kullanici hakkinda ogrenilen bilgiler |
| `activity_logs` | Aktivite kayitlari |
| `favorites` | Favoriler |
| `preferences` | Tercihler (JSON) |
| `cron_jobs` | Zamanlanmis gorevler |

## Gelistirme

```bash
make install    # uv sync
make test       # uv run pytest tests/ -v
make lint       # uv run ruff check graphbot/
make format     # uv run ruff format graphbot/
make run        # uvicorn --reload
make clean      # __pycache__ temizle
```

129 test, 12 test dosyasi.

## Teknolojiler

| Bilesen | Teknoloji |
|---------|-----------|
| Agent | LangGraph StateGraph |
| LLM | LiteLLM (multi-provider) |
| API | FastAPI + Uvicorn |
| WebSocket | FastAPI WebSocket |
| Memory | SQLite (WAL mode) |
| RAG | FAISS + sentence-transformers |
| Config | YAML + pydantic-settings + .env |
| Background | APScheduler |
| CLI | Typer + Rich |
| Lint | Ruff |
| Paket | uv |

## Dokumantasyon

- [`mimari_kararlar.md`](./mimari_kararlar.md) — 9 mimari karar ve gerekceleri
- [`howtowork-development-plan.md`](./howtowork-development-plan.md) — Detayli implementasyon plani
- [`todo.md`](./todo.md) — Faz ilerleme takibi
- [`CLAUDE.md`](./CLAUDE.md) — AI asistan kurallari
