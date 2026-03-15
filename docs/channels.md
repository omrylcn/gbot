# Channels — Multi-Channel Messaging Architecture

GBot supports multiple messaging channels through a unified webhook + send pattern. Each channel is a FastAPI router that receives incoming messages and a send function for outgoing delivery.

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

Every channel follows the same pattern:

```python
# 1. Router definition
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

**Why no classes:** Telegram has no class, WhatsApp has no class. Route-based handler + plain function is sufficient. Creating a BaseChannel abstract class would be over-engineering.

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

Each user has their own Telegram bot token:

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

- Markdown → HTML conversion (`md_to_html`)
- `**bold**` → `<b>`, `*italic*` → `<i>`, `` `code` `` → `<code>`
- HTML parse error → plain text fallback

### Key Files

| File | Function |
|------|----------|
| `gbot/core/channels/telegram.py` | `telegram_webhook()`, `send_message()`, `md_to_html()` |

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
      - "120363407143421687@g.us"  # gbot group
    allowed_dms: []  # empty = no DMs processed
```

### Identity

Single WAHA session, owner's phone connected:

```
user_channels:
  user_id: "owner"
  channel: "whatsapp"
  channel_user_id: "905546718645"  # Phone number
  metadata: {}
```

### Telegram vs WhatsApp

```
TELEGRAM                          WHATSAPP (WAHA)
─────────                         ──────────────
Each user has own bot token →     Single phone, single WAHA →
  self-service                      owner-managed
Separate bot account →            Same phone number →
  identity is clear                 [gbot] prefix required
```

### Webhook Flow — Group Message

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

| `respond_to_dm` | `monitor_dm` | `allowed_dms` | Behavior |
|:---:|:---:|:---:|----------|
| false | false | — | DMs completely ignored (default) |
| false | true | ["905..."] | DMs from listed numbers are stored in session |
| true | — | ["905..."] | DMs from listed numbers get `[gbot]` replies |
| true | — | [] | No DMs answered (allowed_dms empty) |

### `[gbot]` Prefix Rules (Architectural Decision #13)

| Scenario | Prefix | Reason |
|----------|--------|--------|
| Owner command "send message" | No | Owner is sending, bot is the tool |
| Bot auto-reply (group/DM) | `[gbot]` | Bot is speaking, recipient should know |
| Bot proactive (reminder/cron) | `[gbot]` | Bot is sending autonomously |

**Loop prevention:** `fromMe=true` + `text.startswith("[gbot]")` → skip

**Background messaging:** Tools created with `make_messaging_tools(background=True)` automatically add `[gbot]` prefix. No prefix in interactive sessions.

### Message Splitting

WhatsApp limit is 4096 characters. Long messages are split at paragraph boundaries (`\n\n`).

### Key Files

| File | Function |
|------|----------|
| `gbot/core/channels/whatsapp.py` | `whatsapp_webhook()`, `whatsapp_webhook_global()`, `send_whatsapp_message()`, `split_message()` |
| `gbot/core/channels/waha_client.py` | `WAHAClient` — `send_text()`, `phone_to_chat_id()`, `chat_id_to_phone()` |

---

## Cross-Channel Messaging

Channel injection mechanism automatically passes channel to tools:

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

This means:
- From WhatsApp: "remind me via telegram" → LLM sets `channel: "telegram"` → preserved
- From WhatsApp: "remind me" (no channel specified) → `channel: "whatsapp"` injected

---

## Proactive Messaging (Scheduler)

When cron/reminder triggers, `_send_to_channel()`:

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

**Note:** WhatsApp proactive messages include `[gbot]` prefix; Telegram does not (bot account is already separate).

---

## `send_message_to_user` Tool

Cross-user messaging tool:

```python
send_message_to_user(target_user, message, channel="telegram")
```

**Routing:**
1. User is resolved (by user_id or name)
2. Channel link is looked up
3. If no link found → fallback: whatsapp → telegram
4. Deliver via channel

**Background prefix:**
- Interactive session → no prefix (owner is using bot as a tool)
- Background/LightAgent → `[gbot]` prefix (bot is acting autonomously)

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

## API & WebSocket

The default channel — used by the CLI (`gbot chat`) and the admin dashboard.

### REST

```
POST /chat              → JSON { user_id, message, channel?, session_id? }
                        ← JSON { response, session_id, token_count }
```

### WebSocket

```
WS /ws/chat/{user_id}   → Persistent connection
                        → Send: JSON { message, session_id? }
                        ← Receive: JSON { response, session_id, ... }
                        ← Receive: system events (reminders, cron results)
```

WebSocket also delivers real-time system events (reminders, cron job results) without polling.

### Key Files

| File | Function |
|------|----------|
| `gbot/api/routes.py` | `POST /chat` endpoint |
| `gbot/api/ws.py` | WebSocket handler, `ConnectionManager` |

---

## Stub Channels (Discord, Feishu)

Discord and Feishu have router stubs that return `501 Not Implemented`. The file structure and config schema are in place — they need the actual bot integration code.

```python
# discord.py / feishu.py
@router.post("/webhooks/discord/{user_id}")
async def discord_webhook(...):
    raise HTTPException(501, "Discord channel not implemented yet")
```

To implement, follow the same pattern as Telegram/WhatsApp: webhook endpoint + send function.

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
        "url": "http://gbot:8000/webhooks/whatsapp/owner",
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
      "body": "Test message"
    }
  }'
```

### Troubleshooting

```bash
# Session status
curl -s "http://localhost:3000/api/sessions/default" \
  -H "X-Api-Key: YOUR_API_KEY" | python3 -m json.tool

# GBot logs
docker logs gbot --since 5m 2>&1 | grep -i whatsapp

# WAHA logs
docker logs waha --since 5m 2>&1 | grep -v health
```
