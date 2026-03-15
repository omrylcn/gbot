# API Reference

GBot runs a FastAPI server on port 8000. All endpoints return JSON.

---

## Authentication

When `auth.jwt_secret_key` is set in config, all endpoints require a Bearer token.

```bash
# Get token
curl -X POST http://localhost:8000/auth/token \
  -d "username=owner&password=yourpassword"

# Use token
curl http://localhost:8000/health \
  -H "Authorization: Bearer <token>"
```

Auth disabled (`jwt_secret_key` empty) → all endpoints work without token.

---

## Public Endpoints

### `GET /health`

Health check.

```json
{ "status": "ok", "agent_ready": true, "version": "1.14.0" }
```

### `POST /auth/token`

Get JWT access token.

| Field | Type | Description |
|-------|------|-------------|
| `username` | string | User ID |
| `password` | string | User password |

```json
{ "access_token": "eyJ...", "token_type": "bearer" }
```

---

## Chat Endpoints

### `POST /chat`

Send a message and get assistant response.

**Request:**
```json
{
  "message": "Hello!",
  "user_id": "owner",
  "session_id": null,
  "channel": "api"
}
```

**Response:**
```json
{
  "response": "Hi! How can I help?",
  "session_id": "ses_abc123"
}
```

### `WS /ws/chat/{user_id}`

WebSocket for real-time chat. Send JSON messages, receive JSON responses + system events.

---

## Session Endpoints

### `GET /sessions/{user_id}?limit=10`

List user's sessions (owner can see all users).

```json
[
  {
    "session_id": "ses_abc123",
    "channel": "telegram",
    "started_at": "2026-03-15T10:00:00",
    "ended_at": null,
    "token_count": 5420
  }
]
```

### `GET /session/{session_id}/history`

Get all messages in a session.

```json
{
  "session_id": "ses_abc123",
  "messages": [
    { "role": "user", "content": "Hello", "created_at": "..." },
    { "role": "assistant", "content": "Hi!", "created_at": "..." }
  ]
}
```

### `GET /session/{session_id}/stats`

Session stats: messages, tokens, context breakdown, tools.

### `POST /session/{session_id}/end`

Manually close a session.

---

## User Endpoints

### `GET /user/{user_id}/context`

Get assembled user context (notes, preferences, favorites).

### `GET /events/{user_id}`

Get undelivered system events and mark them as delivered.

---

## Admin Endpoints

All admin endpoints require owner role. Prefix: `/admin`.

### Overview

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/admin/status` | Server status (version, model, users, active sessions) |
| `GET` | `/admin/config` | Sanitized config (API keys masked) |
| `GET` | `/admin/stats` | Comprehensive stats (context, tools, sessions, data) |
| `GET` | `/admin/logs?limit=50` | Recent delegation logs |

### Users

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/admin/users` | List all users with roles and channels |
| `PUT` | `/admin/users/{user_id}/role` | Set user role (`{"role": "member"}`) |

### Cron Jobs

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/admin/crons` | List all cron jobs |
| `DELETE` | `/admin/crons/{job_id}` | Remove a cron job |

### Tools

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/admin/tools` | List all tools with groups and availability |
| `GET` | `/admin/skills` | List discovered skills |

### Context Inspection

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/admin/context/{profile}/layers` | Layer breakdown (profile: main/planner/light) |
| `GET` | `/admin/context/{profile}/preview` | Full rendered context string |
| `PUT` | `/admin/context/{profile}/layers/{layer}` | Override a layer at runtime |
| `DELETE` | `/admin/context/{profile}/layers/{layer}` | Clear a layer override |
| `GET` | `/admin/context/overrides` | List all active overrides |
| `DELETE` | `/admin/context/overrides?profile=main` | Clear all overrides |

### Agent Profiles

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/admin/profiles` | List agent profiles (main, planner, light) |
| `GET` | `/admin/profiles/{name}` | Profile detail with AGENT.md content |

---

## Webhook Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/webhooks/telegram/{user_id}` | Telegram bot webhook |
| `POST` | `/webhooks/whatsapp/{user_id}` | WhatsApp (WAHA) webhook |
| `POST` | `/webhooks/whatsapp` | WhatsApp global webhook (resolves user from phone) |

See [channels.md](channels.md) for webhook flow details.

---

## Error Responses

```json
{
  "detail": "Error message here"
}
```

| Status | Meaning |
|--------|---------|
| 400 | Bad request (invalid role, session already closed) |
| 401 | Unauthorized (missing or expired token) |
| 403 | Forbidden (not owner for admin endpoints, not session owner) |
| 404 | Not found (user, session) |
| 429 | Rate limited |
| 500 | Internal server error |
