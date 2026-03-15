# Configuration Guide

GBot uses a layered configuration system: YAML file + environment variables + `.env` file.

**Priority:** env vars > `.env` > YAML > defaults

---

## Quick Start

```bash
# 1. Copy example config
cp config/config.example.yaml config/config.yaml

# 2. Set secrets in .env
echo 'OPENROUTER_API_KEY=sk-or-...' >> .env
echo 'JWT_SECRET_KEY=your-32-char-random-string' >> .env

# 3. Start
gbot run
```

---

## Config File Structure

Config lives at `config/config.yaml` (gitignored — never commit secrets).

```yaml
assistant:
  name: "GBot"
  owner:
    username: "owner"
    name: "Your Name"
  workspace: ./workspace
  model: "openrouter/google/gemini-3-flash-preview"
  temperature: 0.7
  thinking: true
  session_token_limit: 30000
  max_iterations: 20
  tools: ["*"]                    # ["*"] = all tools, or list specific groups
  # system_prompt: "..."          # Overrides AGENT.md if set

providers:
  openrouter:
    api_key: "${OPENROUTER_API_KEY}"
  anthropic:
    api_key: "${ANTHROPIC_API_KEY}"
  openai:
    api_key: "${OPENAI_API_KEY}"
  deepseek:
    api_key: "${DEEPSEEK_API_KEY}"
  groq:
    api_key: "${GROQ_API_KEY}"
  gemini:
    api_key: "${GEMINI_API_KEY}"

channels:
  telegram:
    enabled: true
    allow_from: []                # Empty = allow all Telegram users
  whatsapp:
    enabled: false
    waha_url: "http://waha:3000"
    session: "default"
    api_key: "${WAHA_API_KEY}"
    respond_to_dm: false
    monitor_dm: false
    allowed_groups:
      - "GROUP_ID@g.us"
    allowed_dms:
      "PHONE": "Name"

tools:
  shell:
    timeout: 60
    restrict_to_workspace: true
  web:
    fetch_shortcuts:
      gold: "https://api.genelpara.com/json/?list=altin"
      weather:istanbul: "https://api.open-meteo.com/v1/..."

auth:
  jwt_secret_key: "${JWT_SECRET_KEY}"
  access_token_expire_minutes: 1440
  rate_limit:
    enabled: true
    requests_per_minute: 60

background:
  cron:
    enabled: true
  heartbeat:
    enabled: false
    interval_s: 1800
  delegation:
    model: "openrouter/google/gemini-3-flash-preview"

database:
  path: "data/gbot.db"
```

---

## Environment Variables

All config values can be overridden via env vars with `GBOT_` prefix and `__` for nesting:

| Env Variable | Config Path | Example |
|-------------|-------------|---------|
| `GBOT_ASSISTANT__MODEL` | `assistant.model` | `openai/gpt-4o` |
| `GBOT_ASSISTANT__TEMPERATURE` | `assistant.temperature` | `0.5` |
| `GBOT_ASSISTANT__SESSION_TOKEN_LIMIT` | `assistant.session_token_limit` | `50000` |
| `GBOT_DATABASE__PATH` | `database.path` | `data/prod.db` |
| `GBOT_PROVIDERS__ANTHROPIC__API_KEY` | `providers.anthropic.api_key` | `sk-ant-...` |
| `GBOT_PROVIDERS__OPENROUTER__API_KEY` | `providers.openrouter.api_key` | `sk-or-...` |
| `GBOT_AUTH__JWT_SECRET_KEY` | `auth.jwt_secret_key` | `random-32-chars` |
| `GBOT_CHANNELS__WHATSAPP__API_KEY` | `channels.whatsapp.api_key` | `waha-key` |

---

## `.env` File

Secrets go in `.env` at repo root (gitignored):

```bash
# LLM Provider Keys
OPENROUTER_API_KEY=sk-or-v1-...
ANTHROPIC_API_KEY=sk-ant-...

# Auth
JWT_SECRET_KEY=your-very-long-random-secret-key

# WhatsApp (WAHA)
WAHA_API_KEY=your-waha-api-key
```

The `${VAR}` syntax in `config.yaml` is resolved from these env vars.

---

## Section Reference

### `assistant`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | `"GBot"` | Bot display name |
| `owner.username` | string | required | Owner's user ID |
| `owner.name` | string | `""` | Owner's display name |
| `workspace` | string | `"./workspace"` | Workspace directory for file tools |
| `model` | string | `"anthropic/claude-sonnet-4-5-20250929"` | LLM model identifier |
| `temperature` | float | `0.7` | LLM temperature |
| `thinking` | bool | `false` | Enable thinking/reasoning mode |
| `session_token_limit` | int | `30000` | Max tokens per session before rotation |
| `max_iterations` | int | `20` | Max LLM ↔ tool cycles per request |
| `tools` | list | `["*"]` | Tool groups to enable (`["*"]` = all) |
| `system_prompt` | string | `null` | Override AGENT.md with custom prompt |

### `providers`

Each provider has `api_key` and optional `api_base`:

```yaml
providers:
  openrouter:
    api_key: "sk-or-..."
    api_base: "https://openrouter.ai/api/v1"  # optional, auto-detected
```

Supported: `anthropic`, `openai`, `openrouter`, `deepseek`, `groq`, `gemini`, `moonshot`

Model naming follows LiteLLM convention: `provider/model-name` (e.g., `openrouter/google/gemini-3-flash-preview`). The `openrouter/` prefix routes through OpenRouter SDK directly.

### `channels`

See [channels.md](channels.md) for detailed channel configuration.

### `tools`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `shell.timeout` | int | `60` | Shell command timeout (seconds) |
| `shell.restrict_to_workspace` | bool | `false` | Limit shell to workspace dir |
| `web.search_api_key` | string | `""` | Tavily API key (optional, DuckDuckGo is default) |
| `web.max_results` | int | `5` | Max web search results |
| `web.fetch_shortcuts` | dict | `{}` | URL shortcuts for `web_fetch` tool |

**Fetch shortcuts** let the bot use short names instead of full URLs:

```yaml
tools:
  web:
    fetch_shortcuts:
      gold: "https://api.genelpara.com/json/?list=altin"
      earthquake: "https://api.orhanaydogdu.com.tr/deprem/kandilli/live"
```

Then `web_fetch("gold")` fetches the gold prices URL.

### `auth`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `jwt_secret_key` | string | `""` | JWT signing key (empty = auth disabled) |
| `access_token_expire_minutes` | int | `1440` | Token lifetime (24 hours) |
| `rate_limit.enabled` | bool | `true` | Enable rate limiting |
| `rate_limit.requests_per_minute` | int | `60` | Requests per minute per user |

**Auth disabled:** When `jwt_secret_key` is empty, all endpoints work without authentication (backward compatible).

### `background`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `cron.enabled` | bool | `true` | Enable cron job scheduler |
| `heartbeat.enabled` | bool | `false` | Enable periodic heartbeat |
| `heartbeat.interval_s` | int | `1800` | Heartbeat interval (seconds) |
| `delegation.model` | string | `""` | LLM for delegation planner (empty = use `assistant.model`) |

### `database`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `path` | string | `"data/gbot.db"` | SQLite database file path |

---

## Other Config Files

| File | Purpose |
|------|---------|
| `config/roles.yaml` | RBAC role definitions (owner/member/guest → tool groups) |
| `config/agents.yaml` | Agent profiles (main/planner/light → AGENT.md, skills) |
| `workspace/AGENT.md` | Main agent identity prompt |
| `workspace/agents/*/AGENT.md` | Per-agent-type prompts |

---

## Config Priority Examples

```bash
# config.yaml says model = "openai/gpt-4o"
# .env says GBOT_ASSISTANT__MODEL=anthropic/claude-sonnet-4-5-20250929
# → Result: anthropic/claude-sonnet-4-5-20250929 (env wins)

# config.yaml has no database section
# → Result: data/gbot.db (default)
```
