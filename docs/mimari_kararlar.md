# GraphBot - Mimari Kararlar

> Son güncelleme: 2026-02-19
> Bu dosya, graphbot projesinin mimari tartışmalarını ve alınan kararları takip eder.

---

## 1. Mevcut Durum: Ne Biliyoruz?

### nanobot (~5,700 satır, 40+ class)

```
nanobot/nanobot/
├── agent/
│   ├── loop.py          # AgentLoop - ana döngü (while True: LLM → tool → LLM)
│   ├── context.py       # ContextBuilder - prompt montajı (AGENTS.md, SOUL.md, memory, skills)
│   ├── memory.py        # MemoryStore - markdown tabanlı (MEMORY.md + günlük notlar)
│   ├── skills.py        # SkillsLoader - YAML frontmatter + markdown skill dosyaları
│   ├── subagent.py      # SubagentManager - arka plan görevleri
│   └── tools/           # 8 tool (read/write/edit/list, shell, web_search, web_fetch, message, spawn, cron)
│       ├── base.py      # Tool ABC + JSON Schema validation
│       └── registry.py  # ToolRegistry (register/execute/get_definitions)
├── bus/                 # MessageBus (async FIFO queues, pub/sub)
├── channels/            # 4 kanal (telegram, discord, whatsapp, feishu)
├── providers/           # LiteLLM multi-provider (10+ provider)
├── session/             # JSONL session storage
├── config/              # Pydantic config + JSON loader
├── cron/                # CronService + CronJob types
├── heartbeat/           # Periyodik wake-up service
├── cli/                 # Typer CLI (670 satır!)
└── utils/               # Helper fonksiyonlar
```

**Güçlü yönler:**
- MessageBus (async pub/sub) → kanallarla agent arasında temiz ayrım
- Tool ABC + Registry → JSON Schema validation, plugin mimarisi
- ContextBuilder → bootstrap dosyaları (AGENTS.md, SOUL.md) + memory + skills birleştirme
- Skills System → YAML frontmatter + progressive loading
- SubagentManager → arka plan görevleri, kısıtlı tool seti
- Multi-channel → Telegram, Discord, WhatsApp, Feishu
- LiteLLM → 10+ provider desteği tek interface'le

**Zayıf yönler:**
- RAG yok (semantic search)
- Structured memory yok (SQLite)
- REST API yok
- LangGraph yok (basit while döngüsü)
- Kullanıcı yönetimi yok (sadece allow_from whitelist)

### ascibot (~1,200 satır, 8 class)

```
ascibot/
├── agent/
│   ├── agent.py         # AsciBot - LangChain create_agent wrapper
│   ├── prompts.py       # Türkçe system prompt
│   └── tools.py         # 9 tool (search, detail, meal, favorites, notes, context)
├── api/                 # FastAPI (routes + auth)
├── rag/                 # RecipeRetriever (FAISS + sentence-transformers)
├── memory/              # MemoryStore (SQLite - 7 tablo, 660 satır)
├── models/              # Pydantic models (Recipe, API models)
├── config.py            # pydantic-settings
├── dependencies.py      # FastAPI DI
├── logging.py           # Rotating file logger
└── main.py              # FastAPI app entry
```

**Güçlü yönler:**
- RAG (FAISS + sentence-transformers)
- SQLite structured memory (users, sessions, messages, meal_logs, preferences, favorites, user_notes)
- FastAPI REST API + auth
- Pydantic data models
- Kullanıcı yönetimi (register, login, onboarding)

**Zayıf yönler:**
- Tek kanal (sadece API)
- LangChain bağımlılığı (create_agent)
- Domain-specific (yemek/tarif)
- Session yönetimi basit (manuel open/close)
- Ölçeklenebilirlik düşünülmemiş

---

## 2. Mevcut Planın Sorunları

### 2.1 Kopyala-yapıştır yaklaşımı, tasarım değil

Plan "nanobot'tan taşı", "ascibot'tan taşı" diyor ama **nasıl birleşecekleri** net değil:

- nanobot'un `agent/loop.py` bir **while döngüsü** → LangGraph StateGraph ile **tamamen farklı** bir yapı olacak. Sadece "taşı" demek yetersiz.
- nanobot'un `agent/memory.py` markdown tabanlı → ascibot'un `memory/store.py` SQLite tabanlı → ikisi aynı `memory/` altında nasıl yaşayacak?
- nanobot'un `agent/context.py` bootstrap dosyaları yüklüyor (AGENTS.md, SOUL.md) → LangGraph node'larında bu nasıl çalışacak?

### 2.2 nanobot'un gerçek güçlü yönleri gizli

| Pattern | nanobot'ta | Planda |
|---------|-----------|--------|
| **MessageBus** (async pub/sub) | `bus/queue.py` - 82 satır, temiz | `core/bus/` olarak taşı ✓ |
| **Tool ABC + Registry** | `tools/base.py` + `registry.py` - JSON Schema validation | `agent/tools/` olarak taşı ✓ |
| **ContextBuilder** | `context.py` - AGENTS.md, SOUL.md, memory, skills birleştirme | **Planda belirsiz!** |
| **Skills System** | YAML frontmatter + progressive loading | `agent/skills/` ama detay yok |
| **SubagentManager** | Arka plan görev, kısıtlı tool seti | `agent/subagent.py` ama LangGraph ile nasıl? |
| **Session (JSONL)** | Hafif, dosya tabanlı | SQLite session ile çakışıyor |

### 2.3 İki farklı session/memory modeli çakışıyor

| | nanobot | ascibot |
|---|---------|---------|
| **Session** | JSONL dosyaları | SQLite `sessions` + `messages` tablosu |
| **Memory** | Markdown dosyaları (MEMORY.md) | SQLite (`meal_logs`, `favorites`, `user_notes`, `preferences`) |
| **User** | `allow_from` whitelist | SQLite `users` tablosu + auth |
| **Context** | Bootstrap dosyaları (AGENTS.md, SOUL.md) | `get_user_context()` SQLite sorgusu |

Plan bunların nasıl birleşeceğini söylemiyor.

### 2.4 LangGraph entegrasyonu yüzeysel

Planda "4 node: load_context, reason, execute_tools, respond" diyor ama:
- nanobot'un `AgentLoop._process_message()` aslında çok daha fazla iş yapıyor: session yönetimi, context build, tool iteration, subagent announcement
- Bu mantığın LangGraph node'larına nasıl dağılacağı belirsiz

---

## 3. Cevaplanması Gereken Sorular

1. ~~**Session modeli ne olacak?**~~ → KARAR ALINDI (bkz. Karar #1)
2. ~~**Memory modeli ne olacak?**~~ → KARAR ALINDI (bkz. Karar #2)
3. ~~**Veri saklama stratejisi?**~~ → KARAR ALINDI (bkz. Karar #3)
4. ~~**Context nasıl oluşacak?**~~ → KARAR ALINDI (bkz. Karar #4)
5. ~~**LangGraph graph tasarımı?**~~ → KARAR ALINDI (bkz. Karar #5)
6. ~~**Tool sistemi?**~~ → KARAR ALINDI (bkz. Karar #6)
7. ~~**RAG, FastAPI, CLI, Config?**~~ → KARAR ALINDI (bkz. Karar #7)
8. **Channel entegrasyonu nasıl olacak?** MessageBus + multi-channel pattern

---

## 4. Alınan Kararlar

### Karar #1: Session Modeli

**Karar:** SQLite primary store, token bazlı session yönetimi

**Schema:**

```sql
-- Kullanıcı (cross-channel identity)
CREATE TABLE users (
    user_id TEXT PRIMARY KEY,
    name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Kanal bağlantısı (bir user, birden fazla kanal)
CREATE TABLE user_channels (
    user_id TEXT NOT NULL,
    channel TEXT NOT NULL,           -- "telegram", "discord", "api"
    channel_user_id TEXT NOT NULL,   -- kanal-spesifik ID
    PRIMARY KEY (channel, channel_user_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- Session (bir konuşma birimi)
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    channel TEXT NOT NULL,           -- hangi kanaldan başladı
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP,
    summary TEXT,                    -- LLM tarafından üretilen özet
    token_count INTEGER DEFAULT 0,   -- toplam token sayısı
    close_reason TEXT,               -- "token_limit", "manual", "idle_timeout"
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- Mesajlar
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,              -- "user", "assistant", "system", "tool"
    content TEXT NOT NULL,
    tool_calls JSON,                 -- LangGraph tool çağrıları (varsa)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
```

**Session yaşam döngüsü:**

- **Primary trigger:** Token limit (varsayılan 30k token)
- Session token limiti aşınca:
  1. LLM'e "bu konuşmayı özetle" denir
  2. `summary` sessions tablosuna yazılır
  3. Session kapatılır (`close_reason = "token_limit"`)
  4. Yeni session açılınca önceki session'ın summary'si system prompt'a eklenir
- **Secondary trigger:** Manuel kapatma (API üzerinden) veya idle timeout (opsiyonel, config'den)

**Neden bu karar:**

- `user_channels` tablosu → multi-channel identity (Telegram'daki 12345 = Discord'daki omrylcn)
- `tool_calls` sütunu → LangGraph tool çağrılarını replay/debug için saklar
- `role: "tool"` → Tool sonuçları da mesaj olarak saklanır (LangGraph standardı)
- Token bazlı → LLM context window ile doğal uyum, zaman bazlı keyfi
- Özet ile geçiş → Kullanıcı bilgisi kaybolmaz, önemli tercihler zaten `user_notes`'ta tool ile kaydediliyor

**nanobot'tan ne alındı:** Kanal bazlı session key mantığı (`channel:chat_id`)
**ascibot'tan ne alındı:** SQLite tabanlı yapılandırılmış storage, user → sessions → messages ilişkisi
**Yeni eklenen:** `user_channels` (cross-channel identity), `token_count`, `close_reason`, `tool_calls`, özet ile session geçişi

---

### Karar #2: Memory Modeli

**Karar:** Tek katman — tüm memory SQLite'da. Markdown memory (nanobot'un MEMORY.md'si) ayrı dosya olarak tutulmayacak.

**Gerekçe:** nanobot'un markdown memory'si sonuçta context'e yükleniyor. SQLite'da bir TEXT sütunu da aynı işi görür. Zaten session için SQLite kullanıyoruz, memory'yi ayrı dosya sisteminde tutmanın teknik avantajı yok.

**Schema:**

```sql
-- Agent'ın serbest hafızası (nanobot'un MEMORY.md karşılığı)
CREATE TABLE agent_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,                    -- NULL ise global (agent'ın genel notu)
    key TEXT NOT NULL,               -- "long_term", "2026-02-06", "project_notes"
    content TEXT NOT NULL,           -- serbest metin
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- Kullanıcı hakkında öğrenilen bilgiler (fact'ler)
CREATE TABLE user_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    note TEXT NOT NULL,              -- "vejetaryen", "eşi acı sevmiyor"
    source TEXT DEFAULT 'conversation', -- "conversation", "onboarding", "tool"
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- Aktivite kaydı (ascibot'un meal_logs genelleştirilmiş hali)
CREATE TABLE activity_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    item_id TEXT,
    item_title TEXT NOT NULL,
    activity_type TEXT DEFAULT 'used', -- "used", "viewed", "completed"
    activity_date DATE DEFAULT CURRENT_DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- Favoriler
CREATE TABLE favorites (
    user_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    item_title TEXT NOT NULL,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, item_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- Tercihler (esnek JSON blob)
CREATE TABLE preferences (
    user_id TEXT PRIMARY KEY,
    data JSON NOT NULL DEFAULT '{}',  -- domain'e göre değişebilir
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
```

**Agent memory erişimi (tool'lar):**

- `write_memory(key, content)` → agent serbest not yazar ("long_term", "2026-02-06", vb.)
- `read_memory(key)` → agent okuyabilir (context builder de otomatik yükler)
- `save_user_note(note)` → kullanıcı hakkında fact kaydet
- `log_activity(item_id, item_title, type)` → aktivite kaydet
- `add_to_favorites(item_id, item_title)` → favori ekle
- `get_user_context()` → tüm memory'yi birleştirip string olarak döndür

**Context'e yüklenme sırası:**

```
System Prompt:
├── Identity (AGENTS.md, SOUL.md → dosyadan)
├── Agent Memory (agent_memory tablosu → "long_term" key)
├── User Context (SQLite'dan birleştirilmiş):
│   ├── user_notes → "Kullanıcı hakkında: ..."
│   ├── recent_activities → "Son aktiviteler: ..."
│   ├── favorites → "Favoriler: ..."
│   └── preferences → "Tercihler: ..."
└── Skills (SKILL.md dosyalarından)
```

**nanobot'tan ne alındı:** Agent'ın kendi notlarını tutma konsepti (MEMORY.md → `agent_memory` tablosu)
**ascibot'tan ne alındı:** Yapılandırılmış kullanıcı verisi (user_notes, activity_logs, favorites, preferences)
**Elenen:** Markdown dosya tabanlı memory (gereksiz karmaşıklık, SQLite aynı işi görür)

---

### Karar #3: Veri Saklama Stratejisi

**Karar:** Her veri tipi için uygun format — "İnsan yazıyorsa → dosya, sistem/agent yazıyorsa → SQLite"

| Veri Tipi | Kim Yazar? | Kim Okur? | Format | Nerede? |
|-----------|-----------|-----------|--------|---------|
| **Skills** | İnsan | Agent (context'e yüklenir) | Markdown (YAML frontmatter) | `workspace/skills/*.md` |
| **Agent identity** | İnsan | Agent (system prompt) | Markdown | `workspace/AGENTS.md`, `SOUL.md` |
| **Config** | İnsan | Sistem | **YAML** | `config.yaml` |
| **Memory** | Agent (tool ile) | Agent (context'e yüklenir) | Structured | SQLite |
| **Session** | Sistem | Sistem | Structured | SQLite |
| **Cron jobs** | Agent/İnsan | Sistem | Structured | SQLite |

**Config formatı:** YAML (JSON değil). Daha okunabilir, yorum satırı destekler.

```yaml
# graphbot config
providers:
  anthropic:
    api_key: "sk-..."
  openai:
    api_key: "sk-..."

agent:
  model: "anthropic/claude-sonnet-4-5-20250929"
  max_tokens: 8192
  temperature: 0.7
  session_token_limit: 30000

channels:
  telegram:
    enabled: true
    token: "bot_token_here"
    allow_from: [12345, 67890]
  discord:
    enabled: false

tools:
  web:
    search_api_key: "brave_key"
  shell:
    timeout: 60
    restrict_to_workspace: false
```

**Skills formatı (nanobot'tan aynen):** YAML frontmatter + Markdown body

```markdown
---
name: "weather"
description: "Hava durumu bilgisi"
requires:
  bins: ["curl"]
  env: ["WEATHER_API_KEY"]
always: false
---

# Weather Skill
...talimatlar...
```

**Neden bu ayrım:**

- **Markdown dosyalar** = insan tarafından yazılır/düzenlenir, versiyon kontrollü (git), agent'ın davranışını tanımlar
- **YAML config** = insan tarafından yazılır ama yapılandırılmış, yorum satırları ile okunabilir, JSON'dan üstün
- **SQLite** = runtime'da üretilen/değişen veri, sorgulanabilir, ilişkisel, tek dosyada taşınabilir

**nanobot'tan değişen:** `config.json` → `config.yaml`
**nanobot'tan korunan:** Skills markdown formatı (YAML frontmatter + MD body), workspace dosyaları (AGENTS.md, SOUL.md)

---

### Karar #4: Context Oluşturma & Agent Hiyerarşisi

**Karar:** Hierarchical assistant pattern — tek ana asistan, altında opsiyonel alt-agent'lar. Context, katmanlı şekilde oluşturulur ve token bütçesi yönetilir.

#### 4.1 Context Katmanları

System prompt şu sırayla monte edilir:

```
System Prompt (~4,000 token bütçe):
├── [1] Identity (~500t)
│   workspace/AGENT.md → kim olduğu, kurallar
│   + runtime bilgi (tarih, saat, model)
│
├── [2] Agent Memory (~500t)
│   agent_memory WHERE key='long_term'
│   → agent'ın serbest notları
│
├── [3] User Context (~1,500t)
│   SQLite'dan birleştirilmiş:
│   ├── user_notes → "eşi acı sevmiyor, vejetaryen"
│   ├── recent_activities → "dün: mercimek çorbası"
│   ├── favorites → "karnıyarık, musakka"
│   └── preferences → {dietary: [...]}
│
├── [4] Previous Session Summary (~500t)
│   sessions.summary WHERE user_id ORDER BY DESC
│   → "Önceki konuşmada: tarif aradı..."
│
├── [5] Active Skills (~1,000t)
│   always:true olan SKILL.md dosyaları
│
└── [6] Skills Index (~200t)
    "Mevcut: weather, github, summarize..."
    (agent read_file ile detay yükler)

Messages (~26,000 token bütçe):
├── [user] "merhaba"
├── [assistant] "selam!"
├── [user] "akşama ne yapsam?"
├── [assistant] → [tool_call: search_items]
├── [tool] "1. Karnıyarık 2. Mercimek..."
├── [assistant] "Şu tarifleri buldum: ..."
└── [user] "yeni mesaj" ← current
```

#### 4.2 Token Bütçe Yönetimi

```
Toplam session limit: 30,000 token
├── System prompt: max 4,000 token
│   (katman bütçeyi aşarsa → truncate)
└── Conversation: kalan ~26,000 token
    (session token limiti aşılınca → özet ile yeni session)
```

Truncate kuralları:
- `user_notes` 50+ tane olmuşsa → son 20'sini al
- `activity_logs` çok uzunsa → son 7 günle sınırla
- `AGENT.md` çok uzunsa → ilk 500 token

#### 4.3 Geliştirici Deneyimi: System Prompt Yönetimi

Üç seviye özelleştirme:

**Seviye 1 — Hızlı başlangıç (sadece config.yaml):**
```yaml
assistant:
  name: "AşçıBot"
  system_prompt: |
    Sen AşçıBot'sun - Türk mutfağı uzmanısın.
    Her zaman Türkçe yanıt ver.
  model: "anthropic/claude-sonnet-4-5-20250929"
```

**Seviye 2 — Workspace (detaylı özelleştirme):**
```yaml
assistant:
  name: "AşçıBot"
  workspace: ./workspace
  model: "anthropic/claude-sonnet-4-5-20250929"
```
```
workspace/
├── AGENT.md           # Kim + kurallar + kişilik
└── skills/
    └── recipes/SKILL.md
```

**Seviye 3 — Kod ile (framework kullanımı):**
```python
from graphbot import GraphBot
bot = GraphBot(config="config.yaml")
```

**Öncelik sırası:**
1. `config.yaml → assistant.system_prompt` (varsa direkt kullan)
2. `config.yaml → assistant.workspace` (varsa dosyaları yükle)
3. `./workspace/AGENT.md` (varsayılan konum)
4. Hiçbiri yoksa → built-in default prompt

#### 4.4 Hierarchical Agent Yapısı

Kullanıcı her zaman **ana asistan** ile konuşur. Alt-agent'lar asistanın iç yetenekleridir, kullanıcı onları görmez.

```
Kullanıcı ←→ GraphBot (ana asistan)
                  │
                  ├── Kendi cevaplar (basit sorular)
                  ├── Chef alt-agent'a delege eder (yemek)
                  ├── Researcher alt-agent'a delege eder (araştırma)
                  └── Nutritionist alt-agent'a delege eder (beslenme)
                  │
                  └── GraphBot sonucu formüle eder → Kullanıcıya cevap
```

**Config yapısı:**

```yaml
# config.yaml

assistant:
  name: "GraphBot"
  workspace: ./workspace
  model: "anthropic/claude-sonnet-4-5-20250929"
  temperature: 0.7
  tools: ["*"]

  # Alt-agent'lar (asistanın yetenekleri)
  agents:
    chef:
      name: "Yemek Uzmanı"
      description: "Tarif arama, yemek planlama, mutfak bilgisi"
      workspace: ./workspace/agents/chef
      model: "anthropic/claude-sonnet-4-5-20250929"
      tools: [search_items, get_item_detail, log_activity, get_favorites]
      mode: "sync"       # sonucu bekle

    researcher:
      name: "Araştırmacı"
      description: "Web araştırması, bilgi toplama"
      workspace: ./workspace/agents/researcher
      model: "anthropic/claude-haiku-4-5-20251001"
      tools: [web_search, web_fetch, read_file, write_file]
      mode: "async"      # arka planda çalışsın
```

**Workspace yapısı:**

```
workspace/
├── AGENT.md                    # Ana asistanın identity'si
├── skills/                     # Ana asistanın skill'leri
│   └── cron/SKILL.md
├── agents/
│   ├── chef/
│   │   ├── AGENT.md            # "Sen yemek uzmanısın..."
│   │   └── skills/
│   │       └── recipes/SKILL.md
│   └── researcher/
│       ├── AGENT.md            # "Sen araştırmacısın..."
│       └── skills/
│           └── web/SKILL.md
└── shared/
    └── skills/                 # tüm agent'ların eriştiği ortak skill'ler
```

**Delegasyon mekanizması:**

Ana asistan `delegate` tool'unu çağırarak alt-agent'a iş verir:
```python
@tool
def delegate(agent: str, task: str) -> str:
    """Alt-agent'a görev ver.
    agent: "chef", "researcher", vb.
    task: "Akşam yemeği için kolay bir tarif bul"
    """
```

İki delegasyon modu:
- **sync**: Ana asistan sonucu bekler, cevabına dahil eder (çoğu durum)
- **async**: Arka planda çalışır, bitince bildirir (nanobot'un spawn pattern'i)

**Tek agent durumu:**
`agents` bloğu yoksa → `delegate` tool'u register edilmez, ana asistan kendi tool'larıyla çalışır. Sıfır karmaşıklık.

**LangGraph mapping:**
- Ana asistan = main compiled StateGraph
- Alt-agent'lar = compiled subgraph'ler
- `delegate` tool → subgraph invocation
- Shared state (SQLite) → hepsi ortak erişir

**nanobot'tan ne alındı:** SubagentManager pattern'i (spawn + announce), workspace dosyaları, skills loader, context builder
**ascibot'tan ne alındı:** User context SQLite sorgusu, structured data injection
**Yeni eklenen:** Token bütçe yönetimi, hierarchical agent config, sync/async delegasyon, öncelik sıralı system prompt kaynakları

---

### Karar #5: LangGraph Graph Tasarımı & Execution Modeli

**Karar:** LangGraph = stateless executor, bizim SQLite = source of truth. İki katmanlı mimari: GraphRunner (orkestratör) + Agent Graph (LangGraph).

#### 5.1 Temel Prensip: Sorumluluk Ayrımı

```
┌──────────────────────────────────────────────────┐
│              Bizim Katman (SQLite)                │
│                                                  │
│  sessions, messages, user_notes, favorites,      │
│  preferences, activity_logs, agent_memory        │
│                                                  │
│  → BİZ yönetiyoruz, BİZ sorguluyoruz            │
│  → LangGraph bunları BİLMEZ                      │
└───────────────────┬──────────────────────────────┘
                    │
          GraphRunner (köprü)
          │ - SQLite'dan oku → state'e ver
          │ - Graph'tan al → SQLite'a yaz
                    │
┌───────────────────▼──────────────────────────────┐
│           LangGraph (executor)                   │
│                                                  │
│  State alır → node'ları çalıştırır → state döner │
│                                                  │
│  → STATELESS (bizim açımızdan)                   │
│  → Checkpoint KULLANMIYORUZ (veya sadece         │
│    crash recovery için, opsiyonel)               │
│  → Bizim DB'yi bilmez, dokunmaz                  │
└──────────────────────────────────────────────────┘
```

**LangGraph checkpoint neden kullanılmıyor:**
- LangGraph formatı değişirse → verimiz kırılır
- Checkpoint DB'si opak → sorgulayamayız (user_notes, favorites vb.)
- LangGraph upgrade = migration kabusu
- Bizim memory katmanı (SQLite) zaten her şeyi tutuyor

**LangGraph checkpoint ne zaman kullanılır (opsiyonel):**
- Sadece crash recovery için (process ortasında ölürse kaldığı yerden devam)
- In-memory veya geçici SQLite ile
- Başarılı tamamlanınca checkpoint önemsiz

#### 5.2 AgentState

```python
from langgraph.graph import MessagesState

class AgentState(MessagesState):
    """
    MessagesState → messages: Annotated[list[BaseMessage], add_messages]
    (otomatik mesaj birleştirme, üzerine yazmaz)
    """
    user_id: str
    session_id: str
    channel: str
    role: str = "guest"                    # RBAC — kullanıcı rolü (Karar #12)
    allowed_tools: set[str] | None = None  # RBAC — izinli tool isimleri
    context_layers: set[str] | None = None # RBAC — izinli context katmanları
    system_prompt: str = ""    # load_context doldurur
    token_count: int = 0       # respond günceller
    iteration: int = 0         # reason artırır
    skip_context: bool = False # background task'larda context yükleme atla
```

#### 5.3 Graph Yapısı

```
START → load_context → reason ←────────────┐
                         │                  │
                   has_tool_calls?           │
                    ┌────┴────┐             │
                    ▼         ▼             │
                 respond   execute_tools ───┘
                    │     (tool sonuçları state'e eklenir,
                    ▼      reason'a geri döner)
                   END
```

**Node'lar:**

| Node | Ne yapar | Girdi | Çıktı |
|------|---------|-------|-------|
| `load_context` | System prompt monte eder (identity + memory + user context + skills) | user_id | system_prompt |
| `reason` | LLM çağrısı (messages + tools) | messages, system_prompt | AI message (text veya tool_calls) |
| `execute_tools` | Tool çağrılarını çalıştırır (delegate dahil) | tool_calls | ToolMessage'lar |
| `respond` | Token count günceller | messages | token_count |

**Conditional edge:** `reason` → tool_calls varsa `execute_tools`, yoksa `respond`
**Max iteration guard:** iteration >= limit → zorla `respond`'a git

#### 5.4 GraphRunner (Orkestratör)

Graph'ın dışında, tüm giriş noktalarını birleştirir:

```
Channel → MessageBus ─┐
CLI ───────────────────┼→ GraphRunner → graph.ainvoke(state) → sonuç
API (FastAPI) ─────────┘      │
                              │ SQLite'dan oku (önce)
                              │ SQLite'a yaz (sonra)
                              │ Token limit kontrol
                              │ Session open/close/summarize
```

**Akış:**
1. SQLite'dan session + history oku
2. State hazırla, `graph.ainvoke(state)` çağır (NO checkpointer, NO thread_id)
3. Graph'ın ürettiği yeni mesajları SQLite'a kaydet
4. Token count güncelle
5. Token limit aşıldıysa → LLM'e "özetle" de, session'ı kapat

**Tool side effects:** Tool'lar (save_user_note, log_activity, add_favorite) çalışırken **direkt bizim SQLite'a yazıyor**. LangGraph bunu normal tool execution olarak görüyor, sonucu state'e ekliyor. Asıl veri bizim DB'de.

#### 5.5 Alt-Agent Subgraph

Her alt-agent **aynı graph yapısının** farklı config ile compile edilmiş hali:

```python
# Aynı graph, farklı workspace/model/tools
main_graph = create_agent_graph(config.assistant)
sub_graphs = {
    name: create_agent_graph(agent_cfg)
    for name, agent_cfg in config.assistant.agents.items()
}
```

`delegate` tool çağrıldığında → ilgili subgraph.invoke() çalışır → sonuç ana agent'a döner.

#### 5.6 Background Task Orchestration (Cron + Heartbeat + Subagent)

> **Not:** Bu bölüm v1.0.0 mevcut durumunu anlatır. Bilinen sorunlar ve planlanan iyileştirme için bkz. [Karar #10](./mimari_kararlar.md#karar-10-background-task-mimarisi--lightagent).

nanobot'un üç background sistemi graphbot'a adapte edildi:

```
┌─────────────────────────────────────────────────────────────┐
│                      FastAPI Lifespan                        │
│                                                             │
│  ┌────────────┐  ┌──────────────┐  ┌─────────────────────┐ │
│  │  Cron      │  │  Heartbeat   │  │  Subagent           │ │
│  │  Scheduler │  │  Service     │  │  Worker             │ │
│  └─────┬──────┘  └──────┬───────┘  └──────────┬──────────┘ │
│        │                │                      │            │
│        ▼                ▼                      ▼            │
│   Zamanlı görev    Periyodik         async delegate         │
│   ("her sabah 9")  kontrol           ("arka planda          │
│   + reminder       ("yapılacak        araştır")             │
│        │            iş var mı?")          │                 │
│        ▼                ▼                 ▼                 │
│   runner.process() runner.process()  runner.process()       │
│   (full graph)     (full graph)      (full graph)           │
│        │                │                 │                 │
│        ▼                ▼                 ▼                 │
│   _send_to_channel Sonuç varsa       Sonuç loga            │
│   (direkt Telegram  kanala gönder    yazılır ⚠️             │
│    API çağrısı)                      (kullanıcıya dönmez)  │
└─────────────────────────────────────────────────────────────┘
```

**CronScheduler (APScheduler + SQLite):**
- Zamanlanmış görevler (agent tool ile runtime'da eklenir/çıkarılır)
- Recurring (CronTrigger) + one-shot reminder (DateTrigger)
- Çalışma zamanı gelince → `runner.process()` ile full graph çağrılır
- Sonucu `_send_to_channel()` ile doğrudan platforma gönderir (Telegram API vb.)
- Reminder: LLM çağırmaz, direkt mesaj gönderir, sonra SQLite'dan temizler

**HeartbeatService:**
- Periyodik wake-up (varsayılan 30dk)
- `workspace/HEARTBEAT.md` dosyasını kontrol eder
- Yapılacak iş varsa → `runner.process()` çağırır
- nanobot'tan aynen, opsiyonel

**SubagentWorker (async delegate pool):**
- Ana asistan `delegate(task="...")` dediğinde `asyncio.create_task()` ile arka planda çalıştırır
- `runner.process()` ile full graph çağrılır
- Bitince sonuç loga yazılır, `done_callback` ile task listesinden çıkar

**⚠️ Bilinen sorunlar (v1.0.0):**
1. **Context kirlenmesi:** Tüm background task'lar ana agent'ın full context'ini yüklüyor (23 tool + 4k system prompt)
2. **Maliyet israfı:** Basit kontrol için bile pahalı model + tam pipeline çalışıyor
3. **Koşulsuz bildirim:** Her cron sonucu HER ZAMAN kanala gönderiliyor (NOTIFY/SKIP yok)
4. **Subagent sonucu kaybolur:** Worker tamamlandığında sonuç kullanıcıya dönmüyor

**Planlanan çözüm (Faz 13):** LightAgent + TaskExecutor mimarisi — izole context, kısıtlı tool, ucuz model, koşullu bildirim. Detay: [Karar #10](./mimari_kararlar.md#karar-10-background-task-mimarisi--lightagent), [development-plan2.md § Faz 13](./development-plan2.md).

**Sorumluluk tablosu:**

| Sorumluluk | Kim? |
|-----------|------|
| Mesaj geçmişi saklama | **Bizim SQLite** |
| User notes, favorites, preferences | **Bizim SQLite** |
| Session yönetimi (open/close/summarize) | **GraphRunner + Bizim SQLite** |
| Token counting & limit | **GraphRunner** |
| Cron job saklama | **Bizim SQLite** |
| Cron zamanlama & tetikleme | **CronScheduler (APScheduler)** |
| Reminder delivery | **CronScheduler → _send_to_channel()** |
| Heartbeat kontrolü | **HeartbeatService** |
| Async delegate | **SubagentWorker (asyncio.Task)** |
| LLM çağrısı | **LangGraph** |
| Tool execution döngüsü | **LangGraph** |
| Conditional routing (tool var mı?) | **LangGraph** |
| Node orchestration | **LangGraph** |

**nanobot'tan ne alındı:** Cron + Heartbeat + SubagentManager pattern'leri
**nanobot'tan elenen:** MessageBus (Karar #8 — FastAPI handler'lar direkt çağırır), custom timer chain (APScheduler ile değiştirildi)
**ascibot'tan ne alındı:** Yok (ascibot'ta background task sistemi yok)
**Yeni eklenen:** APScheduler entegrasyonu, one-shot reminder (DateTrigger), `_send_to_channel()` direkt delivery, SQLite-first job persistence
**Kritik karar:** LangGraph checkpoint'i veri saklama için KULLANILMIYOR — sadece execution engine olarak kullanılıyor

---

### Karar #6: Tool Sistemi

**Karar:** LangGraph/LangChain native tool formatı. nanobot'un custom Tool ABC'si kullanılmayacak.

**Gerekçe:**
- Pydantic ile otomatik JSON Schema üretimi (elle yazmak gereksiz)
- LangGraph tool execution ile direkt uyumlu
- `@tool` decorator ile basit, `BaseTool` class ile detaylı kontrol
- nanobot'un kendi validation'ı gereksiz (Pydantic zaten yapıyor)

**Basit tool (`@tool` decorator):**
```python
from langchain_core.tools import tool

@tool
def search_items(query: str, max_results: int = 5) -> str:
    """Bilgi tabanında arama yapar."""
    ...
```

**Detaylı tool (`BaseTool` class):**
```python
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

class SearchItemsInput(BaseModel):
    query: str = Field(description="Arama sorgusu")
    max_results: int = Field(default=5)

class SearchItemsTool(BaseTool):
    name = "search_items"
    description = "Bilgi tabanında arama yapar"
    args_schema = SearchItemsInput

    def _run(self, query: str, max_results: int = 5) -> str:
        ...
```

**nanobot'tan elenen:** Custom Tool ABC, custom JSON Schema validation, custom ToolRegistry
**Korunan konsept:** Tool'ların dependency injection ile memory/retriever erişimi

---

### Karar #7: RAG, FastAPI, CLI, Config

#### 7.1 RAG Sistemi

**Karar:** ascibot'un RecipeRetriever'ı genelleştirilmiş SemanticRetriever olacak. İlerde context manager ve long-term memory için de kullanılabilir.

- `Recipe` → `dict` (domain-agnostic)
- `_recipe_to_text()` → config'den gelen `text_template` ile
- Dinamik add/remove desteği
- Config'den yönetilen: embedding model, data source, index path, text template

```yaml
rag:
  embedding_model: "intfloat/multilingual-e5-small"
  data_source: ./data/items.json
  index_path: ./data/faiss_index
  text_template: "{title}. {description}. Kategori: {category}. Etiketler: {tags}"
  id_field: "id"
```

#### 7.2 FastAPI / REST API

**Karar:** FastAPI sürekli çalışan ana servis olacak. Sadece API endpoint'i değil, aynı zamanda scheduler ve background task'ları yöneten canlı yapı.

- GraphRunner, CronService, HeartbeatService, ChannelManager hepsi FastAPI lifespan içinde başlar
- Endpoints: chat, sessions, users, items, health
- Auth: ascibot'tan adapte (register, login, onboarding)

#### 7.3 CLI

**Karar:** Typer CLI olacak ama öncelik değil. FastAPI servis olarak ana giriş noktası. CLI ileride eklenir (onboard, status, cron manage, quick chat).

#### 7.4 Config Schema

**Karar:** YAML dosya + Pydantic validation. Zorunlu.

- `config.yaml` → insan okur/yazar
- Pydantic `Config` modeli → runtime'da validate eder
- Tüm config değerleri type-safe ve documented

---

### Karar #8: FastAPI Mimari & Channel Entegrasyonu

**Karar:** FastAPI sürekli çalışan ana servis. MessageBus kalkıyor. Channel'lar webhook/WebSocket ile FastAPI'ye bağlanıyor. Her request izole (request-scoped DI).

#### 8.1 Neden Bu Değişiklik?

nanobot'un mevcut sorunları:
- Global mutable state (tool context race condition)
- Sequential message processing (bus üzerinden tek consumer)
- Channel'lar kendi polling loop'larını yönetiyor (karmaşık lifecycle)
- Streaming yok (LLM tamamlanana kadar kullanıcı bekler)
- Auth/rate limit yok

FastAPI bunları çözer:
- **Request-scoped DI** → her request kendi context'ini taşır, race condition yok
- **Concurrent handling** → birden fazla kullanıcı aynı anda
- **Webhook mode** → channel'lar POST atar, polling loop gereksiz
- **WebSocket native** → streaming response + real-time events
- **Middleware chain** → auth, rate limit, logging declarative

#### 8.2 MessageBus Neden Kalkıyor?

nanobot'ta MessageBus gerekli çünkü tek async consumer var. graphbot'ta her giriş noktası doğrudan `GraphRunner.process()` çağırır:

```
API request      → runner.process(user_id, "api", message)
Telegram webhook → runner.process(user_id, "telegram", message)
Discord webhook  → runner.process(user_id, "discord", message)
WebSocket        → runner.process(user_id, "ws", message)
Cron trigger     → runner.process(user_id, "cron", message)
Heartbeat        → runner.process(user_id, "heartbeat", message)
```

Arada queue'ya gerek yok. FastAPI'nin kendi async handler'ları concurrency'yi yönetiyor.

#### 8.3 Genel Yapı

```
┌───────────────────────────────────────────────────────────┐
│                    FastAPI App                             │
│                                                           │
│  REST Endpoints:                                          │
│  ├── POST /chat                  → tek seferlik chat      │
│  ├── POST /chat/{session_id}     → session'lı chat        │
│  ├── GET  /sessions              → session listesi        │
│  ├── CRUD /cron/jobs             → cron yönetimi          │
│  ├── GET  /status                → health + metrics       │
│  └── POST /webhooks/{channel}    → webhook receiver       │
│                                                           │
│  WebSocket Endpoints:                                     │
│  ├── WS /ws/chat                 → streaming chat         │
│  └── WS /ws/events               → subagent/cron events  │
│                                                           │
│  Tüm endpoint'ler ──→ GraphRunner.process() ──→ LangGraph │
│                              │                            │
│                        (request-scoped)                   │
│                        - ContextBuilder                   │
│                        - Tool instances                   │
│                        - Session access                   │
│                                                           │
│  Background Services (lifespan):                          │
│  ├── CronScheduler         → zamanlanmış görevler         │
│  ├── HeartbeatService      → periyodik kontrol            │
│  └── SubagentWorker        → async delegate pool          │
│                                                           │
│  Middleware: Auth → RateLimit → Logging                   │
│                                                           │
│  SQLite (source of truth):                                │
│  sessions, messages, users, user_channels,                │
│  agent_memory, user_notes, activity_logs,                 │
│  favorites, preferences, cron_jobs                        │
└───────────────────────────────────────────────────────────┘
```

#### 8.4 Request-Scoped Agent Context

```python
# FastAPI dependency injection
async def get_runner(
    db: MemoryStore = Depends(get_db),
    config: Config = Depends(get_config),
) -> GraphRunner:
    """Her request kendi GraphRunner'ını alır."""
    return GraphRunner(db=db, config=config)

@app.post("/chat")
async def chat(request: ChatRequest, runner: GraphRunner = Depends(get_runner)):
    result = await runner.process(
        user_id=request.user_id,
        channel="api",
        message=request.message,
    )
    return {"response": result, "session_id": result.session_id}
```

#### 8.5 Channel Webhook Entegrasyonu

**Telegram:**
```python
@app.post("/webhooks/telegram")
async def telegram_webhook(update: dict, runner: GraphRunner = Depends(get_runner)):
    user_id = resolve_user(channel="telegram", channel_user_id=str(update["message"]["from"]["id"]))
    message = update["message"]["text"]
    result = await runner.process(user_id, "telegram", message)
    await telegram_api.send_message(update["message"]["chat"]["id"], result)
```

**Discord:** Aynı pattern, `/webhooks/discord` endpoint'i.
**WhatsApp:** WebSocket bridge kalır (`WS /ws/channels/whatsapp`), Baileys zorunluluğu nedeniyle.

#### 8.6 Streaming Response

```python
@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket, runner: GraphRunner = Depends(get_runner)):
    await ws.accept()
    while True:
        data = await ws.receive_json()

        async for event in runner.process_stream(
            user_id=data["user_id"],
            channel="ws",
            message=data["message"],
        ):
            if event["type"] == "token":
                await ws.send_json({"type": "token", "content": event["content"]})
            elif event["type"] == "tool_start":
                await ws.send_json({"type": "tool_start", "name": event["name"]})
            elif event["type"] == "tool_result":
                await ws.send_json({"type": "tool_result", "result": event["result"]})
            elif event["type"] == "done":
                await ws.send_json({"type": "done", "response": event["response"]})
```

LangGraph'ın `astream()` metodu ile token-by-token akış.

#### 8.7 Background Services

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    cron = CronScheduler(db, runner)
    heartbeat = HeartbeatService(runner, config)
    subagent_pool = SubagentWorker(runner)

    cron_task = asyncio.create_task(cron.start())
    heartbeat_task = asyncio.create_task(heartbeat.start())

    yield

    # Shutdown
    cron_task.cancel()
    heartbeat_task.cancel()
    await subagent_pool.shutdown()
```

| İş Tipi | Mekanizma |
|---------|-----------|
| Kısa (fire-and-forget) | `FastAPI BackgroundTasks` |
| Uzun (async delegate) | `SubagentWorker` (asyncio.TaskGroup) |
| Periyodik (cron/heartbeat) | `CronScheduler` / `HeartbeatService` (lifespan tasks) |

#### 8.8 Elenen: Redis

Başlangıçta Redis gereksiz. SQLite yeterli:
- Concurrent read → SQLite WAL mode ile sorunsuz
- Tek instance deployment → Redis overhead'i gereksiz
- İleride horizontal scaling gerekirse → PostgreSQL + Redis eklenir

**nanobot'tan ne alındı:** Channel kavramı (Telegram, Discord, WhatsApp, Feishu), webhook handler pattern'leri, background task ayrımı
**nanobot'tan elenen:** MessageBus (gereksiz — FastAPI handler'lar direkt çağırır), polling-based channel loop'ları, global mutable tool context
**ascibot'tan ne alındı:** FastAPI app yapısı, REST endpoint pattern'leri, auth middleware, lifespan pattern
**Yeni eklenen:** Request-scoped DI, webhook mode channel'lar, WebSocket streaming, background task pool ayrımı, middleware chain

---

## 5. Genel Akış: Kaynak Haritası

Tüm kararlar alındı. Şimdi büyük resim — her parça nereden geliyor:

### 5.1 nanobot'tan Alınanlar (adapte edilerek)

| Parça | nanobot'taki Hali | graphbot'taki Hali |
|-------|------------------|-------------------|
| **Context Builder** | `agent/context.py` — AGENTS.md, SOUL.md, memory, skills birleştirme | Aynı mantık, SQLite user context eklendi, token bütçe yönetimi eklendi |
| **Skills System** | `agent/skills.py` — YAML frontmatter + progressive loading | Aynen korunuyor, workspace yapısı ile |
| **Cron Service** | `cron/service.py` — JSON dosyada job saklama | SQLite'a taşındı, APScheduler ile entegre |
| **Heartbeat** | `heartbeat/service.py` — periyodik wake-up | Aynen, FastAPI lifespan task olarak |
| **Subagent** | `agent/subagent.py` — background task, kısıtlı tool seti | LangGraph subgraph olarak, async delegate pool |
| **Channel Pattern** | `channels/base.py` + telegram/discord/whatsapp/feishu | Webhook mode'a dönüştü, BaseChannel yerine endpoint handler'lar |
| **LLM Provider** | `providers/litellm_provider.py` — multi-provider | LangChain ChatLiteLLM veya direkt LiteLLM, streaming destekli |
| **Workspace Dosyaları** | `AGENTS.md`, `SOUL.md`, `HEARTBEAT.md` | `AGENT.md` (birleştirilmiş), `HEARTBEAT.md`, skills dizini |
| **Config Pattern** | `config/schema.py` — Pydantic models | Aynı pattern, JSON → YAML'a geçti |
| **Shell Safety** | `tools/shell.py` — deny patterns, timeout | Aynı güvenlik kuralları, LangChain tool formatında |

### 5.2 ascibot'tan Alınanlar (adapte edilerek)

| Parça | ascibot'taki Hali | graphbot'taki Hali |
|-------|------------------|-------------------|
| **SQLite Memory** | `memory/store.py` — 7 tablo (users, sessions, messages, meal_logs, preferences, favorites, user_notes) | Genelleştirildi: meal_logs → activity_logs, recipe → item, user_channels eklendi, agent_memory eklendi |
| **RAG / Retriever** | `rag/retriever.py` — RecipeRetriever, FAISS + sentence-transformers | SemanticRetriever olarak genelleştirildi, config-driven text_template |
| **FastAPI App** | `main.py` — lifespan, CORS, static files | Ana servis olarak genişletildi: webhook, WebSocket, background services |
| **REST Endpoints** | `api/routes.py` — chat, session, user, search | Genişletildi: cron CRUD, webhooks, status, streaming |
| **Auth** | `api/auth.py` — register, login, onboarding | Adapte edilecek + JWT/API key middleware |
| **User Context** | `memory/store.py:get_user_context()` — SQLite sorgusu | Aynı mantık, ContextBuilder'a entegre |
| **Pydantic Models** | `models/api.py`, `models/recipe.py` | Genelleştirildi: Recipe → Item, domain-agnostic |
| **Tool Pattern** | `agent/tools.py` — search_recipes, log_meal, favorites | Aynı tool'lar genelleştirilmiş isimlerle, LangChain @tool formatında |

### 5.3 Yeni / Özgün Çözümler

| Parça | Açıklama |
|-------|---------|
| **LangGraph StateGraph** | nanobot'un while loop'u yerine. Node-based execution, conditional edges, subgraph support |
| **GraphRunner** | Orkestratör katmanı — bus yerine request-scoped, FastAPI DI ile |
| **Token-Based Session** | 30k token limiti, LLM özet ile session geçişi, close_reason tracking |
| **Cross-Channel Identity** | `user_channels` tablosu — Telegram ID + Discord ID = aynı kullanıcı |
| **Hierarchical Agent** | Ana asistan + alt-agent'lar, `delegate` tool ile sync/async delegasyon |
| **Request-Scoped DI** | Her request izole context — race condition yok, concurrent safe |
| **Webhook Channel Mode** | Polling loop yerine platform webhook POST → endpoint handler |
| **Streaming Response** | WebSocket üzerinden token-by-token LLM akışı + tool event'leri |
| **Token Bütçe Yönetimi** | System prompt ~4k + conversation ~26k, katman bazlı truncate |
| **YAML Config** | JSON yerine YAML, Pydantic validation ile |
| **Unified SQLite** | Session + memory + cron + user data tek DB'de, LangGraph checkpoint kullanılmıyor |

### 5.4 Elenenler

| Parça | Nereden | Neden Elendi |
|-------|---------|-------------|
| **MessageBus** | nanobot | FastAPI handler'lar direkt GraphRunner'ı çağırıyor, queue gereksiz |
| **Custom Tool ABC** | nanobot | LangChain/LangGraph native tool formatı yeterli |
| **Markdown Memory** | nanobot | SQLite aynı işi görüyor, ayrı dosya sistemi gereksiz |
| **JSONL Sessions** | nanobot | SQLite daha sorgulanabilir ve ilişkisel |
| **Polling Channel Loops** | nanobot | Webhook mode daha temiz, FastAPI native |
| **JSON Config** | nanobot | YAML daha okunabilir, yorum destekli |
| **LangChain create_agent** | ascibot | LangGraph StateGraph daha kontrollü |
| **Domain-Specific Models** | ascibot | Recipe → dict/Item, genelleştirildi |
| **Redis** | - | MVP'de gereksiz, SQLite yeterli. Scale gerekince eklenir |
| **LangGraph Checkpoint** | - | Veri saklama için kullanılmıyor, SQLite source of truth |

### 5.5 Son Mimari Diyagramı

```
┌────────────────────────────────────────────────────────────────────┐
│                        FastAPI App                                 │
│                                                                    │
│  Giriş Noktaları:                                                  │
│  ┌──────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ │
│  │ REST API │ │  WebSocket   │ │   Webhooks   │ │     CLI      │ │
│  │ /chat    │ │  /ws/chat    │ │  /webhooks/* │ │  (ileride)   │ │
│  └────┬─────┘ └──────┬───────┘ └──────┬───────┘ └──────┬───────┘ │
│       └──────────────┬┼───────────────┘                │          │
│                      ││                                │          │
│                      ▼▼                                │          │
│               ┌──────────────┐                         │          │
│               │ GraphRunner  │◄────────────────────────┘          │
│               │ (req-scoped) │                                    │
│               └──────┬───────┘                                    │
│                      │                                            │
│          ┌───────────┼───────────┐                                │
│          ▼           ▼           ▼                                │
│   ┌────────────┐ ┌────────┐ ┌────────────┐                       │
│   │  Context   │ │SQLite  │ │  LangGraph │                       │
│   │  Builder   │ │  R/W   │ │  Agent     │                       │
│   │            │ │        │ │  Graph     │                       │
│   │ - identity │ │sessions│ │            │                       │
│   │ - memory   │ │messages│ │ load_ctx   │                       │
│   │ - user ctx │ │users   │ │   ↓        │                       │
│   │ - skills   │ │notes   │ │ reason ←─┐ │                       │
│   └────────────┘ │favs    │ │   ↓      │ │                       │
│                  │prefs   │ │ tools? ──┘ │                       │
│                  │activity│ │   ↓        │                       │
│                  │cron    │ │ respond    │                       │
│                  └────────┘ └────────────┘                       │
│                                                                    │
│  Background Services (lifespan):                                   │
│  ┌──────────────┐ ┌──────────────┐ ┌────────────────────────────┐│
│  │CronScheduler │ │HeartbeatSvc  │ │SubagentWorker              ││
│  │(APScheduler) │ │(periyodik)   │ │(asyncio.TaskGroup)         ││
│  └──────────────┘ └──────────────┘ └────────────────────────────┘│
│                                                                    │
│  Dosya Sistemi:                          SQLite DB (15 tablo):     │
│  ┌────────────────────┐                ┌──────────────────┐       │
│  │ config.yaml        │                │ users            │       │
│  │ roles.yaml (RBAC)  │                │ user_channels    │       │
│  │ workspace/         │                │ sessions         │       │
│  │   AGENT.md         │                │ messages         │       │
│  │   HEARTBEAT.md     │                │ agent_memory     │       │
│  │   skills/          │                │ user_notes       │       │
│  │     *.md           │                │ activity_logs    │       │
│  │   agents/          │                │ favorites        │       │
│  │     chef/AGENT.md  │                │ preferences      │       │
│  │     .../AGENT.md   │                │ cron_jobs        │       │
│  └────────────────────┘                │ cron_exec_log    │       │
│                                        │ reminders        │       │
│                                        │ system_events    │       │
│                                        │ background_tasks │       │
│                                        │ api_keys         │       │
│                                        └──────────────────┘       │
└────────────────────────────────────────────────────────────────────┘
```

### 5.6 Mesaj İşleme Akışı (End-to-End)

```
1. Kullanıcı mesaj gönderir (API/WebSocket/Telegram/Discord)
       │
2. FastAPI endpoint handler mesajı alır
       │
3. GraphRunner.process() çağrılır (request-scoped)
       │
4. SQLite'dan session kontrolü:
   ├── Aktif session var mı? → token limiti aşıldı mı?
   │   ├── Evet (limit aşıldı) → özetle, kapat, yeni session aç
   │   └── Hayır → mevcut session kullan
   └── Session yok → yeni session oluştur
       │
5. SQLite'dan session history yükle → messages listesi
       │
6. LangGraph graph.ainvoke(state) çağrılır:
   │
   ├── [load_context] → ContextBuilder:
   │   ├── AGENT.md (dosyadan)
   │   ├── agent_memory (SQLite'dan)
   │   ├── user_context (SQLite'dan: notes, activities, favs, prefs)
   │   ├── prev session summary (SQLite'dan)
   │   └── skills (dosyadan: always=true olanlar + index)
   │
   ├── [reason] → LLM çağrısı (system_prompt + messages + tools)
   │   │
   │   ├── Tool calls varsa → [execute_tools]
   │   │   ├── Normal tool → çalıştır (search, web, memory write...)
   │   │   ├── delegate sync → subgraph.invoke() → sonuç
   │   │   └── delegate async → SubagentWorker'a ver → arka plan
   │   │   │
   │   │   └── → [reason]'a geri dön (loop)
   │   │
   │   └── Tool calls yoksa → [respond]
   │
   └── [respond] → token count güncelle
       │
7. Graph sonucu döner
       │
8. SQLite'a yaz:
   ├── user message kaydet
   ├── assistant message kaydet (+ tool_calls)
   ├── tool messages kaydet
   └── session token_count güncelle
       │
9. Response'u kullanıcıya gönder:
   ├── REST → JSON response
   ├── WebSocket → streaming tokens (astream ile)
   ├── Telegram → telegram_api.send_message()
   └── Discord → discord_api.send_message()
```

---

## 6. İmplementasyon Faz Sıralaması & Gerekçesi

### 6.1 Neden Bu Sıra?

Faz sıralaması iki prensibe göre belirlendi:

1. **Bağımlılık zinciri:** Her faz, kendinden öncekine bağımlı. Tool'lar agent'sız çalışmaz, background services FastAPI'siz çalışmaz.
2. **Core-first, extras-last:** Önce çalışan bir agent pipeline, sonra ek özellikler. RAG gibi bağımsız özellikler en sona.

### 6.2 Faz Tablosu

| Faz | İçerik | Bağımlılık | Gerekçe |
|-----|--------|-----------|---------|
| **0** | Proje iskeleti | — | Dizin yapısı, pyproject.toml, config.yaml şablonu |
| **1** | Config + Memory Store | Faz 0 | Her şeyin bağımlılığı: YAML config yükleme, SQLite CRUD |
| **2** | LangGraph Agent (temel) | Faz 1 | Core execution pipeline: state → nodes → graph → runner. Tool'suz basit mesaj → LLM → response |
| **3** | Tool System | Faz 2 | Agent'ın elleri: memory tools, search, filesystem, shell, web, delegate, cron. LangGraph native format |
| **4** | FastAPI + REST API | Faz 2 | Web server: REST endpoints, WebSocket streaming, request-scoped DI. Background services'ın yaşadığı yer (lifespan) |
| **5** | Background Services | Faz 4 | Cron (APScheduler), heartbeat, subagent worker — hepsi FastAPI lifespan'de başlar. Dinamik scheduling (agent tool ile runtime'da job ekleme) |
| **6** | Skills System | Faz 2 | Context enrichment: YAML frontmatter + MD skills, progressive loading, ContextBuilder entegrasyonu |
| **7** | Channel Entegrasyonu | Faz 4 | Webhook mode: Telegram/Discord/WhatsApp → FastAPI endpoint → runner.process(). Cross-channel identity |
| **8** | CLI | Faz 2 | Typer CLI: agent start, status, cron manage, quick chat. Düşük öncelik |
| **9** | RAG | Faz 1 | Ek özellik: FAISS semantic search. Long-term memory ve bilgi tabanı için. Core akıştan bağımsız, istediğin zaman eklenebilir |
| **10** | Entegrasyon & Polish | Tümü | End-to-end test, error handling, logging, README |

### 6.3 Kritik Sıralama Kararları

**FastAPI neden Faz 4 (erken)?**
- Background services (cron, heartbeat, spawn) FastAPI lifespan'ında yaşıyor
- Channel webhook handler'ları FastAPI endpoint'leri
- Request-scoped DI ile concurrent request handling
- Agent çalışır çalışmaz API üzerinden test edilebilir

**Background Services neden ayrı faz (Faz 5)?**
- nanobot'un dinamik scheduling özelliğini korumak önemli — agent runtime'da cron job oluşturabiliyor
- nanobot'un custom `_arm_timer → asyncio.sleep → tick → rearm` zinciri yerine **APScheduler** kullanılacak (production-tested, miss handling, drift koruması, job store persistence)
- Spawn/subagent: nanobot'un `asyncio.create_task()` + custom while loop yerine **asyncio.TaskGroup** veya `BackgroundTasks`
- Bu mekanizmalar FastAPI lifespan'e bağlı, o yüzden FastAPI'den sonra

**RAG neden Faz 9 (en son)?**
- Core agent akışından bağımsız — search_items tool'u RAG olmadan da mock data ile çalışır
- Ek özellik niteliğinde: long-term memory, bilgi tabanı, context enrichment
- FAISS + sentence-transformers ağır dependency'ler — erken eklemenin faydası yok
- İstediğin zaman bağımsız olarak eklenebilir

---

## 7. Sonuç

Toplam **12 mimari karar** alındı:

| # | Karar | Özet |
|---|-------|------|
| 1 | Session Modeli | SQLite, token bazlı (30k), özet ile geçiş |
| 2 | Memory Modeli | Tek katman SQLite, markdown memory elendi |
| 3 | Veri Saklama | İnsan → dosya (md/yaml), sistem → SQLite |
| 4 | Context & Agent Hiyerarşisi | Katmanlı context, hierarchical assistant, token bütçe |
| 5 | LangGraph Graph | Stateless executor, GraphRunner orkestratör, background tasks |
| 6 | Tool Sistemi | LangGraph native (@tool, BaseTool) |
| 7 | RAG/API/CLI/Config | Genel SemanticRetriever, FastAPI ana servis, YAML+Pydantic |
| 8 | FastAPI & Channels | Webhook mode, request-scoped DI, streaming, MessageBus elendi |
| 9 | User Yönetimi & Owner | Config'de owner, CLI ile user ekleme, DB bazlı erişim kontrolü |
| 10 | Background Task Mimarisi & LightAgent | İzole, hafif, dinamik agent'lar — koşullu bildirim, model hiyerarşisi |
| 11 | Auth & API Güvenliği | JWT + API key, geriye uyumlu (kapalı default), rate limiting |
| 12 | RBAC (Role-Based Access Control) | 3 rol (owner/member/guest), tool grupları, context katman filtreleme |

---

### Karar #9: User Yönetimi & Owner Konsepti

**Problem:** Mevcut sistem Telegram'dan gelen mesajlarda `telegram_8062223398` gibi yapay user_id'ler oluşturuyor. Bu ID:
- Kanal-spesifik (aynı kişi farklı kanaldan gelirse farklı user olur)
- LLM tarafından bilinmiyor (tool'lara yanlış user_id geçiliyor)
- Anlamsız (insanın okuyabileceği bir isim değil)

**Karar:** User gerçek bir entity. Config'de owner (varsayılan kullanıcı) tanımlanır, ek kullanıcılar CLI ile eklenir.

#### 9.1 Temel Prensip

```
user_id (kök entity)
  ├── user_channels (telegram_id, discord_id, ... → bu user'a bağlı)
  ├── sessions (telegram session, api session, cli session)
  ├── user_notes
  ├── favorites
  ├── preferences
  ├── activity_logs
  └── cron_jobs / reminders
```

**User_id her şeyin kökü.** Kanallar sadece transport. Session'lar, notlar, favoriler, reminder'lar hep bir user'a ait.

#### 9.2 Owner (Varsayılan Kullanıcı)

Config'de `assistant.owner` tanımlanır:

```yaml
assistant:
  name: "GraphBot"
  owner:
    username: "omrylcn"
    name: "Ömer"
```

- Sistem başlarken owner user DB'de oluşturulur (yoksa)
- Owner belirtilmezse → eski davranış korunur (channel_id bazlı yapay user — geriye uyumluluk)
- Owner = varsayılan user. Herhangi bir yerde user_id gerekirken belirtilmezse owner kullanılır

#### 9.3 Kullanıcı Ekleme (CLI)

```bash
# Owner zaten config'den geliyor, otomatik oluşur

# Yeni kullanıcı ekle + kanal bağla
graphbot user add ali --name "Ali" --telegram 555666777

# Sadece kullanıcı ekle (kanal sonra bağlanır)
graphbot user add ayse --name "Ayşe"

# Kanal bağla
graphbot user link ayse telegram 888999000

# Kullanıcıları listele
graphbot user list

# Kullanıcı sil
graphbot user remove ali
```

#### 9.4 Erişim Kontrolü (allow_from kalkıyor)

**Eski sistem:**
```yaml
channels:
  telegram:
    allow_from: [12345, 67890]  # config'de statik liste
```

**Yeni sistem:**
```yaml
channels:
  telegram:
    enabled: true
    token: "xxx"
    # allow_from yok — erişim DB'den kontrol edilir
```

Erişim kontrolü:
```
Telegram mesajı geliyor (telegram_id: 123456)
  → user_channels tablosunda ara
  → Bulunduysa → o user'ın session'ına yönlendir
  → Bulunamadıysa → reddet (401/403)
```

**Config = altyapı** (bot token'ları, enabled/disabled)
**DB = kullanıcı verisi** (kim hangi kanalda, erişim kontrolü)

#### 9.5 Channel Akışı (Değişen)

**Telegram:**
```
Mesaj (telegram_id: 8062223398) geliyor
  → user_channels WHERE channel='telegram' AND channel_user_id='8062223398'
  → user_id='omrylcn' bulundu
  → session = get_active_session('omrylcn', 'telegram')
  → runner.process('omrylcn', 'telegram', message, session_id)
```

**API:**
```
POST /chat {message: "merhaba"}
  → user_id belirtilmemişse → owner kullanılır
  → session = get_active_session('omrylcn', 'api')
```

**CLI:**
```
graphbot chat -m "merhaba"
  → owner kullanılır
  → session = get_active_session('omrylcn', 'cli')
```

#### 9.6 Context'e user_id Ekleme

System prompt'a runtime bilgisi eklenir:
```
# Runtime
- Current user_id: omrylcn
- Current time: 2026-02-07 17:45
- Use this user_id when calling tools that require it.
```

Bu sayede LLM tool'lara doğru user_id'yi geçirir.

#### 9.7 Değişecek Dosyalar

| Dosya | Değişiklik |
|-------|-----------|
| `core/config/schema.py` | `OwnerConfig` model, `AssistantConfig.owner` field, `allow_from` opsiyonel/deprecated |
| `config.yaml` | `assistant.owner` eklenir, `allow_from` kaldırılır |
| `api/app.py` (lifespan) | Startup'ta owner user oluştur + kanal bağla |
| `core/channels/base.py` | `resolve_or_create_user` → DB'den resolve, bulamazsa reddet |
| `core/channels/telegram.py` | Owner/user bazlı erişim kontrolü |
| `agent/context.py` | Runtime user_id bilgisi eklenir |
| `cli/commands.py` | `user` subcommand grubu: add, list, remove, link |
| `api/routes.py` | POST /chat → owner default |

#### 9.8 Geriye Uyumluluk

- Owner config'de yoksa → eski davranış (kanal bazlı yapay user_id)
- Mevcut testler bozulmaz (test'ler kendi user_id'lerini zaten explicit veriyor)
- `allow_from` alanı config'den kaldırılmaz, ama kullanılmaz (deprecated)

**Neden bu karar:**
- User_id artık anlamlı bir identifier (username, insanın okuyabileceği)
- Tek kaynak: DB (config'de kullanıcı listesi yönetmek ölçeklenmiyor)
- CLI ile yönetim: basit, scriptlenebilir
- LLM doğru user_id'yi biliyor → tool'lar doğru çalışıyor
- Kişisel bot için basit (sadece owner), büyüyebilir (arkadaş ekleme)

---

### Karar #10: Background Task Mimarisi & LightAgent

**Problem:** Mevcut sistemde cron job, subagent ve reminder hepsi aynı ağır GraphRunner'ı çağırıyor. Bu 4 somut soruna yol açıyor:

#### 10.1 Problem Analizi

**Problem 1 — Context kirlenmesi:**
```
Kullanıcı 10 gündür yemek sohbeti yapıyor.
Cron job: "altın fiyatını kontrol et"
  → load_context: yemek tercihleri, favori tarifler, yemek geçmişi yükleniyor
  → LLM bu context'i görüyor → altın kontrolüyle hiç ilgisi yok
  → Gereksiz token tüketimi + potansiyel karışıklık
```

Gerçek hayat analojisi: İlaç alarmın çaldığında tüm iş bağlamını hatırlamana gerek yok. Sadece "ilaç al" yeter. LLM için de aynısı.

**Problem 2 — Maliyet israfı:**
```
Her 10 dk'da bir altın kontrolü:
  → 23 tool tanımı LLM'e gönderiliyor (~2000 token)
  → Full system prompt (~4000 token)
  → Pahalı model (claude-sonnet) çalışıyor
  → Günde 144 çağrı × ~6000 token = ~864K token/gün (sadece bir alert için!)

Olması gereken:
  → 1 tool (web_search) (~100 token)
  → Kısa prompt (~200 token)
  → Ucuz model (gpt-4o-mini)
  → Günde 144 × ~300 token = ~43K token/gün (20x ucuz)
```

**Problem 3 — Spam (koşulsuz bildirim):**
```
Cron tetiklenince:
  runner.process() → LLM cevap üretir → HER ZAMAN channel'a gönderir

  09:00  "Altın şu an 195k, henüz 200k değil"    ← spam
  09:10  "Altın şu an 196k, henüz 200k değil"    ← spam
  09:20  "Altın şu an 198k, henüz 200k değil"    ← spam
  09:30  "Altın 201k oldu!"                        ← asıl istenen
```

Koşullu bildirim mekanizması yok.

**Problem 4 — Subagent sonucu kaybolur:**
```
Ana agent: delegate(task="veriyi analiz et")
Worker: runner.process(task) → sonuç loga yazılır
Kullanıcıya: "Görevi arka plana attım: a1b2c3d4"
... sonra? HİÇBİR ŞEY. Sonuç kullanıcıya dönmüyor.
```

#### 10.2 Mevcut Durum Karşılaştırması

| | **Nanobot** | **GraphBot (v1.0)** | **Sorun** |
|---|---|---|---|
| **Subagent** | Ayrı instance, izole context, kısıtlı tool, sonuç geri döner | Aynı runner, aynı tool, sonuç kaybolur | Sonuç yok, izolasyon yok |
| **Cron job** | Ana agent + full context | Aynı runner + boş session | İkisinde de ağır |
| **Reminder** | Ana agent üzerinden (gereksiz ağır) | Direkt mesaj, LLM yok | GraphBot daha iyi |
| **Alert** | Desteklemiyor | Desteklemiyor | İkisinde de yok |

Nanobot subagent'ta context izolasyonunu çözmüş ama cron'da çözmemiş. GraphBot'ta hiçbirinde çözülmemiş.

#### 10.3 Temel Fark: Subagent vs Cron

Subagent ve cron aslında **aynı şeyin farklı trigger'ları:**

```
Subagent  = "bunu ŞİMDİ yap, ben beklemeyeceğim"   (immediate trigger)
Cron      = "bunu SONRA yap, şu zamanda/periyodik"  (scheduled trigger)
Reminder  = "şu mesajı SONRA gönder"                (scheduled, LLM yok)
Alert     = "koşul sağlanınca bildir"                (scheduled + conditional)
```

Çalıştırma mekanizması aynı — ikisi de bir task'ı bağımsız çalıştırıyor. Fark sadece ne zaman tetikleneceği.

#### 10.4 Çözüm: LightAgent + Unified Task Executor

Tek bir `TaskExecutor` katmanı, farklı trigger mekanizmalarıyla:

```
┌────────────────────────────────────────────────────────┐
│                  TaskExecutor (ortak)                    │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │ LightAgent                                        │   │
│  │  - İzole context (ana sohbetten bağımsız)        │   │
│  │  - Kısıtlı tool seti (sadece gerekli olanlar)    │   │
│  │  - Kendi prompt'u (task-focused)                  │   │
│  │  - Kendi model'i (ana agent seçer)                │   │
│  │  - Kendi küçük graph'ı                            │   │
│  └──────────────────────────────────────────────────┘   │
│                         │                                │
│            ┌────────────┼────────────┐                   │
│            │            │            │                   │
│     ┌──────┴──────┐ ┌──┴───┐ ┌──────┴──────┐           │
│     │  Immediate  │ │ Cron │ │   Alert     │           │
│     │  (delegate) │ │      │ │ (conditional)│           │
│     └─────────────┘ └──────┘ └─────────────┘           │
│                                                          │
│  Sonuç yönlendirme:                                     │
│  ├── → Channel'a gönder (cron/alert)                    │
│  ├── → Ana agent'a bildir (subagent)                    │
│  └── → Sessiz geç (alert koşul sağlanmadı)             │
└────────────────────────────────────────────────────────┘
```

#### 10.5 LightAgent Tanımı

```python
class LightAgent:
    """Tek amaçlı, hafif, izole agent."""

    def __init__(
        self,
        prompt: str,           # task-focused talimat
        tools: list,           # sadece gerekli tool'lar (1-3)
        model: str,            # ucuz veya görev uygun model
    ):
        self.prompt = prompt
        self.tools = tools
        self.model = model
        self._graph = self._compile()  # kendi küçük graph'ı

    async def run(self, message: str) -> str:
        """History yok, context yok. Sadece çalış ve cevap ver."""
        state = {
            "messages": [
                SystemMessage(content=self.prompt),
                HumanMessage(content=message),
            ],
            "iteration": 0,
        }
        result = await self._graph.ainvoke(state)
        return self._extract_response(result)
```

LightAgent özellikleri:
- **İzole:** Ana sohbet history'sini görmez
- **Hafif:** 1-3 tool, kısa prompt, minimal token
- **Tek amaçlı:** Bir görevi yapar, bitirir
- **Ucuz:** Ana agent model seçimini yapar, genellikle mini model yeter

#### 10.6 Model Hiyerarşisi — Ana Agent Karar Verir

Ana agent (akıllı model) LightAgent oluştururken görevin karmaşıklığına göre model seçer:

```
Ana Agent (claude-sonnet / gpt-4o)
  │
  │  Kullanıcı: "altın 200k olunca uyar"
  │
  │  Karar verir:
  │    → tools: [web_search]
  │    → model: gpt-4o-mini        ← basit iş, ucuz model yeter
  │    → prompt: "fiyat kontrol et, >= 200k → NOTIFY"
  │    → schedule: "*/10 * * * *"
  │
  ▼
LightAgent (gpt-4o-mini)
  → Her 10 dk: web_search → parse → SKIP veya NOTIFY
```

Model seçim kuralları (ana agent'ın system prompt'unda tanımlı):

```
simple   → gpt-4o-mini      # fiyat kontrol, hava durumu, basit sorgu
moderate → gpt-4o            # analiz, karşılaştırma, çoklu kaynak
complex  → claude-sonnet     # araştırma, çok adımlı reasoning
```

Bu kararı **ana agent** veriyor çünkü görevin karmaşıklığını o anlıyor. LightAgent kendi model'ini bilmez — kendisine ne atandıysa onu kullanır.

#### 10.7 Alert Akışı (Altın 200k Senaryosu)

```
Kullanıcı: "altın 200k olunca uyar"
     │
     ▼
Ana Agent (claude-sonnet):
  create_alert tool'unu çağırır:
  {
    prompt: "Gram altın fiyatını kontrol et.
             >= 200000 TL ise NOTIFY:{fiyat} döndür.
             < 200000 ise SKIP döndür.",
    tools: ["web_search"],
    model: "gpt-4o-mini",
    schedule: "*/10 * * * *",
    channel: "telegram",
    notify_once: true
  }
     │
     ▼
TaskExecutor: LightAgent oluşturur + APScheduler'a kaydeder + SQLite'a persist eder
     │
Ana Agent → kullanıcıya: "Tamam, 10 dakikada bir kontrol edeceğim."
     │
     ▼ (her 10 dakikada bir)
     │
LightAgent (gpt-4o-mini) çalışır:
  1. web_search("gram altın fiyatı TL") → "195.000 TL"
  2. 195000 < 200000 → "SKIP"
  3. TaskExecutor: SKIP → sessiz geç, kullanıcıya mesaj YOK
     │
  ... günler geçer ...
     │
  1. web_search("gram altın fiyatı TL") → "201.500 TL"
  2. 201500 >= 200000 → "NOTIFY:Gram altın 201.500 TL'ye ulaştı!"
  3. TaskExecutor: NOTIFY → Telegram'a gönder + job sil (notify_once)
     │
     ▼
Kullanıcı Telegram'da: "Gram altın 201.500 TL'ye ulaştı!"
```

#### 10.8 3 Ayrı Kavram — Cron / Reminder / Background Task

Bu 3 kavram temelden farklıdır ve ayrı tablolarda, ayrı mantıkla ele alınmalıdır:

| | **Cron Job** | **Reminder** | **Background Task** |
|---|---|---|---|
| **Ne zaman** | Tekrarlayan schedule | Tek sefer, zamanlı | Hemen, arka planda |
| **Çalıştıran** | LightAgent / fonksiyon | Mesaj gönder (LLM yok) | LightAgent / fonksiyon |
| **Persist** | Evet (SQLite) | Evet (SQLite) | Hayır (memory) |
| **Session ilişkisi** | Yok — user + channel | Yok — user + channel | Var — parent session + fallback channel |
| **Sonuç** | Channel'a gönder / logla | Channel'a gönder | Session'a dön / channel fallback |
| **Koşul** | Opsiyonel (NOTIFY/SKIP) | Yok | Yok |
| **Yaşam süresi** | Silinene kadar | Tetiklenene kadar | Bitene kadar |

**Session Sahipliği Kuralı:**
- **Cron / Reminder → User + Channel** (session yok). Teslim adresi channel.
- **Background Task → Session** (channel fallback). Sonucu önce parent session'a döner, session kapandıysa channel'a gönderir.

#### 10.9 Nanobot'tan Ne Alınıyor, Ne Değişiyor

| Nanobot Pattern | Alınıyor mu? | Değişiklik |
|---|---|---|
| Subagent izole context | ✅ Evet | Aynen — LightAgent kendi prompt + tool + message listesi |
| Subagent kısıtlı tool seti | ✅ Evet | Ana agent dinamik olarak belirler (önceden tanımlı değil) |
| Subagent sonuç → MessageBus | ✅ Konsept | MessageBus yok → SQLite event veya direkt callback |
| Cron → ana agent + full context | ❌ Hayır | Cron → LightAgent (izole, hafif) |
| Önceden tanımlı subagent'lar | ❌ Hayır | Dinamik oluşturma — senaryo sınırsız |

#### 10.10 SQLite Schema — Ayrı Tablolar

3 ayrı kavram, 4 ayrı tablo. Detay: [background_task_analiz.md § 11](./background_task_analiz.md)

```sql
-- 1. Cron Jobs (zamanlı, tekrarlayan)
CREATE TABLE cron_jobs (
    job_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    cron_expr TEXT,
    message TEXT NOT NULL,
    channel TEXT DEFAULT 'api',           -- teslim adresi
    enabled BOOLEAN DEFAULT TRUE,
    created_in_session TEXT,              -- bilgi amaçlı
    agent_prompt TEXT,                    -- NULL ise full runner (eski davranış)
    agent_tools JSON,
    agent_model TEXT,
    notify_condition TEXT,                -- 'always' | 'notify_skip'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- 2. Cron Execution Log
CREATE TABLE cron_execution_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    result TEXT,
    status TEXT DEFAULT 'success',        -- 'success', 'error', 'skipped'
    tokens_used INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    FOREIGN KEY (job_id) REFERENCES cron_jobs(job_id)
);

-- 3. Reminders (tek sefer, LLM yok)
CREATE TABLE reminders (
    reminder_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    message TEXT NOT NULL,
    channel TEXT DEFAULT 'telegram',      -- teslim adresi
    run_at TEXT NOT NULL,
    status TEXT DEFAULT 'pending',        -- 'pending', 'sent', 'cancelled'
    created_in_session TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sent_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- 4. Background Tasks (ephemeral, session-bound)
CREATE TABLE background_tasks (
    task_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    parent_session TEXT,                  -- sonucu buraya dön
    fallback_channel TEXT,                -- session kapanırsa teslim adresi
    task_description TEXT NOT NULL,
    status TEXT DEFAULT 'running',        -- 'running', 'completed', 'failed'
    agent_prompt TEXT,
    agent_tools JSON,
    agent_model TEXT,
    result TEXT,
    error TEXT,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    duration_ms INTEGER,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
```

#### 10.11 Uygulama Planı

Bu karar aşağıdaki fazları etkiler:

| Faz | Etki |
|---|---|
| **Faz 12 (Prompting)** | Ana agent'ın system prompt'una LightAgent oluşturma talimatları eklenir |
| **Yeni: Background Task Refactoring** | Mevcut cron_jobs → background_tasks, LightAgent class, TaskExecutor |
| **Faz 16 (Monitoring)** | LightAgent çalışma logları, maliyet tracking |

Uygulama sırası (3 katmanlı):
1. **Katman 1 (Quick wins):** NOTIFY/SKIP + skip_context → ~20 satır, mevcut scheduler'a
2. **Katman 2 (Genişlet):** cron_jobs tablosuna agent sütunları + cron_execution_log + reminders tablosu ayır
3. **Katman 3 (Gerekirse):** LightAgent class + TaskExecutor + background_tasks tablosu + create_alert tool

Detaylı plan: [background_task_analiz.md § 7](./background_task_analiz.md)

**Neden bu karar:**
- Context izolasyonu → her background task sadece kendi işini bilir
- Maliyet kontrolü → ucuz model + minimal token
- Esneklik → doğal dilde her koşul ifade edilebilir
- Spam engelleme → NOTIFY/SKIP mekanizması
- Dinamik → önceden tanımlı değil, ana agent runtime'da oluşturur
- Basit ama katmanlı → reminder basit kalır, alert akıllıdır

---

### Karar #11: Auth & API Güvenliği

> Detaylı uygulama planı: [development-plan2.md § Faz 11](./development-plan2.md)

**Problem:** Mevcut API endpoint'leri tamamen açık. Herhangi bir auth mekanizması yok — password hash yok, JWT token yok, rate limiting yok. Channel webhook'ları `allow_from` config listesiyle filtreleniyor ama API tarafı korumasız.

**Karar:** İki bağımsız auth stratejisi: JWT (API) + webhook'lar bağımsız. Geriye uyumlu — auth kapalı default.

#### 11.1 İki Auth Katmanı

```
API Endpoint'ler (REST + WebSocket):
  → JWT token (Bearer header)
  → VEYA API key (X-API-Key header)
  → auth kapalıyken → pass-through (mevcut davranış)

Channel Webhook'lar (Telegram, Discord, vb.):
  → Kendi mekanizmaları (bot token, allow_from, user_channels DB)
  → JWT ile İLGİSİZ — bağımsız kalır
```

**Neden iki ayrı katman:**
- Webhook'lar platform tarafından authenticate ediliyor (Telegram bot token, Discord signatures)
- Webhook endpoint'lere JWT eklemek gereksiz karmaşıklık ve dış bağımlılık
- API kullanıcıları ile channel kullanıcıları farklı senaryolar

#### 11.2 Geriye Uyumluluk: `jwt_secret_key=""` → Auth Kapalı

```yaml
auth:
  jwt_secret_key: ""        # ← boş string = auth tamamen kapalı
```

Bu durumda:
- Tüm API endpoint'leri açık (mevcut davranış)
- `get_current_user` dependency → herkes için geçerli (owner user döner)
- Mevcut testler bozulmaz
- Tek kullanıcılı/geliştirme senaryosu için ideal

Secret key ayarlandığında:
- Register/login → JWT access token döner
- API endpoint'leri `Authorization: Bearer <token>` gerektirir
- Token'sız request → 401 Unauthorized

#### 11.3 Auth Akışı

```
1. Register: POST /auth/register {username, password, name}
     → password_hash = bcrypt(password)
     → users tablosuna kaydet
     → JWT token döndür

2. Login: POST /auth/token {username, password}
     → bcrypt.verify(password, password_hash)
     → JWT {sub: user_id, exp: now+24h} üret
     → token döndür

3. Authenticated Request: POST /chat
     Headers: Authorization: Bearer <jwt_token>
     → verify_token() → user_id çıkar
     → runner.process(user_id, ...)

4. API Key (alternatif): POST /chat
     Headers: X-API-Key: <api_key>
     → api_keys tablosunda ara
     → user_id bul → aynı akış
```

#### 11.4 Rate Limiting

In-memory rate limiter (slowapi kullanmadan):

```python
# Basit sliding window counter
rate_limits = {}  # {ip: [(timestamp, count), ...]}

# Config'den:
# rate_limit.requests_per_minute: 60
# rate_limit.burst: 10
```

**Neden slowapi değil:**
- Tek bir basit decorator yeterli
- Ek dependency gereksiz
- In-memory yeterli (tek instance, restart'ta sıfırlanır — kabul edilebilir)

#### 11.5 Dependency'ler

- `PyJWT>=2.8.0` — JWT encode/decode (hafif, tek iş yapan kütüphane)
- `passlib[bcrypt]>=1.7.4` — Password hashing (bcrypt backend)

**Neden bu kütüphaneler:**
- PyJWT: python-jose'dan daha hafif, sadece JWT yapıyor
- passlib: bcrypt'in doğrudan kullanımı yerine — timing-safe compare, future-proof hash format

#### 11.6 DB Değişiklikleri

```sql
-- users tablosuna eklenen sütunlar
ALTER TABLE users ADD COLUMN password_hash TEXT;
ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user';  -- 'user', 'admin'

-- Yeni tablo: API keys
CREATE TABLE api_keys (
    key_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    key_hash TEXT NOT NULL,         -- bcrypt hash (plain key sadece oluşturulurken gösterilir)
    name TEXT,                      -- "My CLI key", "CI/CD key"
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,           -- NULL = süresiz
    is_active BOOLEAN DEFAULT TRUE,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
```

**Neden bu tasarım:**
- `password_hash` NULL olabilir → mevcut kullanıcılar etkilenmez
- `role` RBAC yetkilendirme için (owner/member/guest — bkz. Karar #12)
- API key hash'leniyor → DB sızsa bile key'ler güvende
- `expires_at` NULL → süresiz key (kişisel kullanım)
- `is_active` → key'i silmeden devre dışı bırakma

**nanobot'tan ne alındı:** Yok (nanobot'ta auth yok)
**ascibot'tan ne alındı:** Register/login pattern'i, password hash konsepti
**Yeni eklenen:** JWT token, API key, rate limiting, geriye uyumlu auth toggle, iki katmanlı auth stratejisi

---

### Karar #12: Role-Based Access Control (RBAC)

> Detaylı tasarım: [rbac_mimari.md](../rbac_mimari.md)

**Problem:** Tüm kullanıcılar aynı tool seti, context ve veriye erişiyor. Owner'ın shell/filesystem erişimi ile guest'in web araması aynı seviyede. `users.role` DB'de var ama hiç kullanılmıyor. `make_tools()` herkese aynı listeyi döndürüyor. ContextBuilder rol farkı gözetmiyor.

**Karar:** 3 rol, grup bazlı tool erişimi, katman bazlı context filtreleme. `roles.yaml` ayrı dosyada tanımlanır. Graph recompile YOK — per-request filtreleme.

#### 12.1 Üç Rol

| Rol | Kim | Nereden gelir |
|-----|-----|---------------|
| **owner** | Sistem sahibi, tam yetki | `config.yaml` → `assistant.owner.username` |
| **member** | Kayıtlı kullanıcı, standart yetki | Admin tarafından atanır |
| **guest** | Tanınmayan/yeni kullanıcı, sınırlı | Default — yeni user oluşturulduğunda |

#### 12.2 Tool Grupları & Erişim Matrisi

Tool'lar tek tek değil, gruplar halinde yönetilir (`roles.yaml`). Yeni tool eklendiğinde grubuna düşer, roller otomatik güncellenir.

| Grup | owner | member | guest |
|------|-------|--------|-------|
| memory (7 tool) | ✅ | ✅ | ❌ |
| search (2 tool) | ✅ | ✅ | ❌ |
| web (2 tool) | ✅ | ✅ | ✅ |
| filesystem (4 tool) | ✅ | ❌ | ❌ |
| shell (1 tool) | ✅ | ❌ | ❌ |
| scheduling (7 tool) | ✅ | ✅ | ❌ |
| messaging (1 tool) | ✅ | ✅ | ❌ |
| delegation (1 tool) | ✅ | ❌ | ❌ |

#### 12.3 Context Katman Filtreleme

| Katman | owner | member | guest |
|--------|-------|--------|-------|
| identity | ✅ | ✅ | ✅ |
| runtime | ✅ | ✅ | ✅ |
| role | ✅ | ✅ | ✅ |
| agent_memory | ✅ | ✅ | ❌ |
| user_context | ✅ | ✅ | ❌ |
| events | ✅ | ✅ | ❌ |
| session_summary | ✅ | ✅ | ❌ |
| skills | ✅ | ✅ | ❌ |

#### 12.4 Teknik Mimari — 3 Katmanlı Filtreleme

```
User mesaj gönderir
    │
    ▼
[Runner] → DB'den user.role al → permissions.get_allowed_tools(role)
    │       → allowed_tools + context_layers set'ini state'e koy
    │
    ▼
[load_context] → permissions.get_context_layers(role)
    │              → sadece izinli katmanları build et
    │
    ▼
[reason] → tool_defs'i allowed_tools ile filtrele
    │        → LLM sadece izinli tool'ları görür
    │
    ▼
[execute_tools] → çağrılan tool allowed_tools'da mı? (double-check)
    │               → LLM hallucination guard
    │
    ▼
Response → kullanıcıya
```

**Neden graph recompile yok:**
- Graph tüm tool'larla bir kez compile edilir
- Per-request filtreleme `reason()` ve `execute_tools()` node'larında yapılır
- Performans: Graph her request'te yeniden oluşturulmaz
- Güvenlik: İki katmanlı kontrol (LLM görmez + execution engellenir)

#### 12.5 roles.yaml — Ayrı Dosya

```yaml
tool_groups:
  memory: [save_user_note, get_user_context, ...]
  web: [web_search, web_fetch]
  filesystem: [read_file, write_file, edit_file, list_dir]
  shell: [exec_command]
  # ...

roles:
  owner:
    tool_groups: [memory, search, web, filesystem, shell, scheduling, messaging, delegation]
    context_layers: [identity, runtime, role, agent_memory, user_context, events, session_summary, skills]
    max_sessions: 0  # unlimited
  member:
    tool_groups: [memory, search, web, scheduling, messaging]
    context_layers: [identity, runtime, role, agent_memory, user_context, events, session_summary, skills]
    max_sessions: 0
  guest:
    tool_groups: [web]
    context_layers: [identity, runtime, role]
    max_sessions: 1

default_role: guest
```

**Neden ayrı dosya:** `config.yaml` zaten kalabalık. Roller bağımsız bir concern. Deploy ortamına göre farklı `roles.yaml` kullanılabilir.

#### 12.6 Session Kısıtlaması

- Guest: max 1 session (aynı session tekrar kullanılır)
- Member/Owner: sınırsız session

#### 12.7 Değişen Dosyalar

| Dosya | Değişiklik |
|-------|-----------|
| `roles.yaml` | **YENİ** — rol tanımları |
| `graphbot/agent/permissions.py` | **YENİ** — YAML loader + tool/context check |
| `graphbot/agent/state.py` | `role`, `allowed_tools`, `context_layers` alanları |
| `graphbot/agent/runner.py` | Rol lookup, allowed_tools hesaplama, guest session |
| `graphbot/agent/nodes.py` | reason() filtreleme, execute_tools() guard |
| `graphbot/agent/context.py` | context_layers parametresi, katman filtreleme |
| `graphbot/memory/store.py` | `set_user_role()` + migration user→member |
| `graphbot/api/admin.py` | PUT /admin/users/{id}/role endpoint |

#### 12.8 Geriye Uyumluluk

- `roles.yaml` yoksa → tüm kullanıcılara "owner" yetkisi (mevcut davranış korunur)
- Mevcut kullanıcıların `role='user'` → migration'da `'member'`'a çevrildi
- Owner kullanıcı `config.yaml`'dan tanımlanıyor → startup'ta `role='owner'` set edilir
- `allowed_tools=None` → hiç filtre yok (eski davranış)

#### 12.9 Gelecek İyileştirmeler (Şimdi Yapılmayacak)

- **Linux-style grup sistemi:** Kullanıcılar birden fazla gruba ait olabilir, izinler union
- **İçerik filtreleme:** Rol bazlı hassas içerik kısıtlama
- **Zaman/kullanım kısıtlamaları:** Saatlik/günlük mesaj limiti per rol
- **Per-user override:** Bireysel kullanıcıya özel izin ekleme/çıkarma
- **JWT'ye rol bilgisi:** Şu an DB'den her seferinde çekiliyor — yeterli

**nanobot'tan ne alındı:** Yok (nanobot'ta RBAC yok)
**ascibot'tan ne alındı:** Yok (ascibot'ta RBAC yok)
**Yeni eklenen:** 3 rol sistemi, grup bazlı tool erişimi, context katman filtreleme, roles.yaml, permissions modülü, 2 katmanlı execution guard

---

### 13. WhatsApp Mesaj Kimliği — `[gbot]` Prefix Kuralları

**Tarih:** 2026-02-22
**Bağlam:** WhatsApp'ta bot bir kişinin telefon numarası üzerinden mesaj gönderiyor (Telegram'da ayrı bot hesabı var). Bu yüzden alıcının "bunu kim gönderdi?" sorusuna cevap verebilmesi lazım.

#### 13.1 Problem

WhatsApp'ta native bot hesabı yok. WAHA üzerinden owner'ın (veya bağlı kullanıcının) telefon numarasıyla mesaj gönderiliyor. Bu durumda:
- Owner "Murat'a mesaj at" dediğinde → mesaj owner'ın numarasından gidiyor
- Bot reminder/cron çalıştığında → yine aynı numaradan gidiyor
- Bot DM'e otomatik cevap verdiğinde → yine aynı numaradan gidiyor

Alıcı açısından hepsi aynı görünüyor. Kim gönderdi belli olmalı.

#### 13.2 Karar: 3 Durum, 2 Davranış

| Durum | Prefix | Neden |
|-------|--------|-------|
| Owner komutu: "mesaj at" | Yok | Owner gönderiyor, bot sadece araç (postacı) |
| Bot oto-cevap (DM/grup) | `[gbot]` | Bot konuşuyor, alıcı bunu bilmeli |
| Bot proaktif (reminder/cron) | `[gbot]` | Bot gönderiyor, owner'ın haberi yok |

**Kural basit:** Bot kendi iradesiyle konuşuyorsa `[gbot]`, owner talimatıyla iletiyorsa prefix yok.

#### 13.3 Uygulama

```python
# Bot oto-cevap (grup mesajına cevap)
await send_whatsapp_message(wa_config, chat_id, f"[gbot] {response}")

# Bot proaktif (scheduler)
await send_whatsapp_message(wa_config, chat_id, f"[gbot] {text}")

# Owner komutu (send_message_to_user tool)
await send_whatsapp_message(wa_config, chat_id, message)  # prefix YOK
```

#### 13.4 Loop Prevention

`[gbot]` prefix'i aynı zamanda loop prevention mekanizması:

```python
# whatsapp.py webhook handler
if is_from_me and text.startswith("[gbot]"):
    return JSONResponse({"ok": True})  # Bot'un kendi mesajını ignore et
```

WAHA `message.any` event'i `fromMe=True` olan mesajları da gönderiyor. Bot'un kendi `[gbot]` cevaplarını tekrar işlememesi için bu kontrol gerekli.

#### 13.5 Telegram Karşılaştırması

Telegram'da bu problem yok çünkü her kullanıcının kendi bot token'ı var — mesajlar zaten bot hesabından gidiyor. WhatsApp'ta tek telefon numarası olduğu için prefix ile ayırt etmek gerekiyor.

```
Telegram: @owner_bot → Murat    (kim gönderdi: bot, açık)
WhatsApp: 0554... → Murat       (kim gönderdi: owner mı, bot mu? → [gbot] ile ayırt)
```

#### 13.6 Gelecek: Multi-Session

Eğer her kullanıcı kendi telefonunu bağlarsa (WAHA multi-session), prefix kuralları aynen geçerli kalır — her kullanıcının kendi numarasından bot `[gbot]` prefix'i ile konuşur.

**Neden bu tasarım:** Kullanıcı deneyimi. Alıcı, mesajın insan mı bot mu gönderdiğini anında ayırt edebilmeli. Aynı zamanda loop prevention için de kullanılıyor — tek mekanizma, iki fayda.
