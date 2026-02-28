# Channels — Multi-Channel Messaging Architecture

GraphBot supports multiple messaging channels through a unified webhook + send pattern. Each channel is a FastAPI router that receives incoming messages and a send function for outgoing delivery.

---

## Supported Channels

| Channel | Status | Transport | Identity |
|---------|--------|-----------|----------|
| **Telegram** | Active | Bot API (HTTP webhook) | Bot token per user |
| **WhatsApp** | Active | WAHA REST API | Phone number (shared) |
| **API/WebSocket** | Active | REST + WS | JWT token |
| **Discord** | Stub (501) | Gateway WebSocket | Bot token |
| **Feishu/Lark** | Stub (501) | lark-oapi SDK | app_id + app_secret |

---

## Architecture Overview

```
┌──────────────┐   webhook    ┌──────────────────────────────────┐
│  Telegram    │ ───────────► │  POST /webhooks/telegram/{uid}   │
│  Bot API     │ ◄─────────── │  send_message(token, chat_id)    │
└──────────────┘   Bot API    └──────────────────────────────────┘

┌──────────────┐   webhook    ┌──────────────────────────────────┐
│  WhatsApp    │ ───────────► │  POST /webhooks/whatsapp/{uid}   │
│  (WAHA)      │ ◄─────────── │  send_whatsapp_message(config)   │
│  :3000       │   REST API   └──────────────────────────────────┘
└──────────────┘

┌──────────────┐   HTTP/WS    ┌──────────────────────────────────┐
│  CLI / Web   │ ───────────► │  POST /chat  |  WS /ws/{uid}    │
│  Client      │ ◄─────────── │  JSON response / WS push         │
└──────────────┘              └──────────────────────────────────┘
```

All channels converge on the same pipeline:

```
Incoming → Webhook Handler → runner.process() → Response → Channel Send
```

---

## Channel Pattern

Her channel aynı pattern'i takip eder:

```python
# 1. Router tanımı
router = APIRouter(tags=["channel_name"])

# 2. Webhook endpoint
@router.post("/webhooks/{channel}/{user_id}")
async def webhook(user_id, request, db, runner):
    link = db.get_channel_link(user_id, "channel")  # Verify user
    text = extract_text(body)                         # Parse payload
    response = await runner.process(...)              # Unified pipeline
    await send_function(config, chat_id, response)    # Deliver

# 3. Send helper (module-level function)
async def send_message(config, chat_id, text):
    # Channel-specific delivery
```

**Neden class yok:** Telegram'da class yok, WhatsApp'ta da yok. Route-based handler + düz fonksiyon yeterli. BaseChannel abstract class oluşturmak over-engineering.

---

## Telegram

### Config

```yaml
channels:
  telegram:
    enabled: true
    allow_from: []  # empty = allow all
```

### Identity

Her kullanıcının kendi Telegram bot token'ı var:

```
user_channels:
  user_id: "owner"
  channel: "telegram"
  channel_user_id: "8445774788:AAF..."  # Bot token
  metadata: {"chat_id": "8062223398"}   # Saved on first message
```

### Webhook Flow

```
Telegram → POST /webhooks/telegram/{user_id}
  ├─ Verify user has telegram link
  ├─ Extract text from update.message.text
  ├─ Save chat_id to metadata (for proactive messaging)
  ├─ Get/create session (channel="telegram")
  ├─ runner.process() → response
  └─ send_message(token, chat_id, response)
```

### Message Formatting

- Markdown → HTML dönüşümü (`md_to_html`)
- `**bold**` → `<b>`, `*italic*` → `<i>`, `` `code` `` → `<code>`
- HTML parse hatası → plain text fallback

### Key Files

| File | Function |
|------|----------|
| `graphbot/core/channels/telegram.py` | `telegram_webhook()`, `send_message()`, `md_to_html()` |

---

## WhatsApp (WAHA)

### Config

```yaml
channels:
  whatsapp:
    enabled: true
    waha_url: "http://waha:3000"
    session: "default"
    api_key: "your-waha-api-key"
    respond_to_dm: false
    monitor_dm: false
    allowed_groups:
      - "120363407143421687@g.us"  # gbot grubu
    allowed_dms: []  # empty = no DMs processed
```

### Identity

Tek WAHA session, owner'ın telefonu bağlı:

```
user_channels:
  user_id: "owner"
  channel: "whatsapp"
  channel_user_id: "905546718645"  # Phone number
  metadata: {}
```

### Telegram vs WhatsApp Farkı

```
TELEGRAM                          WHATSAPP (WAHA)
─────────                         ──────────────
Her user kendi bot token'ı →      Tek telefon, tek WAHA →
  self-service                      owner-managed
Bot hesabı ayrı →                 Aynı telefon numarası →
  kimlik belli                      [gbot] prefix gerekli
```

### Webhook Flow — Grup Mesajı

```
WAHA → POST /webhooks/whatsapp/{user_id}
  ├─ Event filtering: only "message" and "message.any" (fromMe)
  ├─ Extract text from payload.body
  ├─ Filter: @c.us (DM) or @g.us (group) — ignore others
  ├─ Group: check allowed_groups whitelist
  ├─ Loop prevention: fromMe + startswith("[gbot]") → skip
  ├─ Get/create session (channel="whatsapp")
  ├─ runner.process() → response
  └─ send_whatsapp_message(config, chat_id, "[gbot] {response}")
```

### Webhook Flow — DM

```
WAHA → POST /webhooks/whatsapp/{user_id}
  ├─ is_group=false → DM handling
  ├─ Check: monitor_dm OR respond_to_dm enabled?
  │   └─ Both false → ignore (default)
  ├─ Check: sender in allowed_dms? (empty list = no DMs)
  ├─ fromMe=true → ignore
  ├─ Resolve sender name from user_channels
  ├─ If respond_to_dm:
  │   ├─ runner.process() → response
  │   └─ send "[gbot] {response}"
  └─ If monitor_dm:
      └─ Store "[WhatsApp DM] {name}: {text}" in session
```

### Global Webhook

```
POST /webhooks/whatsapp  (no user_id)
  ├─ Only @g.us messages (DMs ignored)
  ├─ Check allowed_groups
  ├─ Extract participant phone
  ├─ resolve_user("whatsapp", phone) → user_id
  └─ Delegate to whatsapp_webhook(user_id, ...)
```

### DM Config Matrix

| `respond_to_dm` | `monitor_dm` | `allowed_dms` | Davranış |
|:---:|:---:|:---:|----------|
| false | false | — | DM tamamen ignore (default) |
| false | true | ["905..."] | Listedeki numaralardan gelen DM'ler session'a kaydedilir |
| true | — | ["905..."] | Listedeki numaralardan gelen DM'lere `[gbot]` ile cevap verilir |
| true | — | [] | Hiç DM'e cevap verilmez (allowed_dms boş) |

### `[gbot]` Prefix Kuralları (Mimari Karar #13)

| Durum | Prefix | Neden |
|-------|--------|-------|
| Owner komutu "mesaj at" | Yok | Owner gönderiyor, bot araç |
| Bot oto-cevap (grup/DM) | `[gbot]` | Bot konuşuyor, alıcı bilmeli |
| Bot proaktif (reminder/cron) | `[gbot]` | Bot gönderiyor |

**Loop prevention:** `fromMe=true` + `text.startswith("[gbot]")` → skip

**Background messaging:** `make_messaging_tools(background=True)` ile oluşturulan tool'lar otomatik `[gbot]` prefix ekler. Interactive session'da prefix yok.

### Message Splitting

WhatsApp limiti 4096 karakter. Uzun mesajlar paragraph sınırlarından (`\n\n`) bölünür.

### Key Files

| File | Function |
|------|----------|
| `graphbot/core/channels/whatsapp.py` | `whatsapp_webhook()`, `whatsapp_webhook_global()`, `send_whatsapp_message()`, `split_message()` |
| `graphbot/core/channels/waha_client.py` | `WAHAClient` — `send_text()`, `phone_to_chat_id()`, `chat_id_to_phone()` |

---

## Cross-Channel Messaging

Channel injection mekanizması tool'lara otomatik channel geçer:

```python
# nodes.py — execute_tools
if "channel" in tool_fields:
    if original:
        # LLM explicitly set channel → keep it
        pass
    else:
        # No channel → inject from session
        args["channel"] = state["channel"]
```

Bu sayede:
- WhatsApp'tan "telegramdan hatırlat" → LLM `channel: "telegram"` verir → korunur
- WhatsApp'tan "hatırlat" (channel belirtmeden) → `channel: "whatsapp"` inject edilir

---

## Proactive Messaging (Scheduler)

Cron/reminder tetiklendiğinde `_send_to_channel()`:

```python
async def _send_to_channel(user_id, channel, text) -> bool:
    if channel == "telegram":
        link = db.get_channel_link(user_id, "telegram")
        send_message(token, chat_id, text)

    elif channel == "whatsapp":
        link = db.get_channel_link(user_id, "whatsapp")
        chat_id = WAHAClient.phone_to_chat_id(link["channel_user_id"])
        send_whatsapp_message(config, chat_id, f"[gbot] {text}")

    else:  # api/ws
        ws_manager.send_event() or db.add_system_event()
```

**Not:** WhatsApp proaktif mesajlarda `[gbot]` prefix eklenir, Telegram'da eklenmez (bot hesabı zaten ayrı).

---

## `send_message_to_user` Tool

Kullanıcılar arası mesajlaşma tool'u:

```python
send_message_to_user(target_user, message, channel="telegram")
```

**Routing:**
1. Kullanıcı bulunur (user_id veya name ile)
2. Belirtilen channel'dan link aranır
3. Link yoksa fallback: whatsapp → telegram
4. Channel'a göre delivery

**Background prefix:**
- Interactive session → prefix yok (owner araç olarak kullanıyor)
- Background/LightAgent → `[gbot]` prefix (bot otonom çalışıyor)

---

## user_channels Table

```sql
CREATE TABLE user_channels (
    user_id TEXT NOT NULL,
    channel TEXT NOT NULL,           -- "telegram", "whatsapp"
    channel_user_id TEXT NOT NULL,   -- bot token / phone number
    metadata TEXT DEFAULT '{}',      -- JSON: {"chat_id": "..."}
    PRIMARY KEY (channel, channel_user_id)
);
```

### Key Methods (MemoryStore)

| Method | Purpose |
|--------|---------|
| `link_channel(uid, channel, channel_uid)` | Register channel identity |
| `resolve_user(channel, channel_uid)` | Channel identity → user_id |
| `get_channel_link(uid, channel)` | Get channel_user_id + metadata |
| `update_channel_metadata_by_user(uid, channel, meta)` | Update metadata (e.g., save chat_id) |

### CLI

```bash
# Link channel
gbot user link owner whatsapp 905546718645
gbot user link murat telegram 8445774788:AAF...

# List users (shows linked channels)
gbot user list
```

---

## WAHA Setup

### Prerequisites

- Docker Compose running
- WAHA service in docker-compose.yml
- config.yaml WhatsApp section configured

### Steps

```bash
# 1. Start containers
docker compose up -d

# 2. WAHA Dashboard → http://localhost:3000
#    Create session with webhook URL

# 3. Create WAHA session
curl -X POST "http://localhost:3000/api/sessions" \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: YOUR_API_KEY" \
  -d '{
    "name": "default",
    "start": true,
    "config": {
      "webhooks": [{
        "url": "http://graphbot:8000/webhooks/whatsapp/owner",
        "events": ["message", "message.any"]
      }]
    }
  }'

# 4. Scan QR code from WAHA dashboard
# 5. Link phone number
gbot user link owner whatsapp 905551234567

# 6. Test
curl -X POST "http://localhost:8000/webhooks/whatsapp/owner" \
  -H "Content-Type: application/json" \
  -d '{
    "event": "message",
    "payload": {
      "from": "905551234567@c.us",
      "fromMe": false,
      "body": "Test mesajı"
    }
  }'
```

### Troubleshooting

```bash
# Session status
curl -s "http://localhost:3000/api/sessions/default" \
  -H "X-Api-Key: YOUR_API_KEY" | python3 -m json.tool

# GraphBot logs
docker logs graphbot --since 5m 2>&1 | grep -i whatsapp

# WAHA logs
docker logs waha --since 5m 2>&1 | grep -v health
```
