# CLI.md — gbot Command Line Interface

`gbot` is the CLI for GraphBot. It provides terminal commands for server management, user administration, and an interactive chat REPL.

## Installation

```bash
uv sync
```

Entry point: `gbot` (alias: `graphbot`)

---

## Quick Start

```bash
# Start the API server
gbot run

# Login (in another terminal)
gbot login owner -p <password>

# Open interactive chat
gbot

# Or send a single message
gbot chat -m "Hello!"

# Check system status
gbot status
```

---

## Commands

### `gbot` (no arguments)

Opens the interactive REPL — same as `gbot chat`.

```bash
gbot
```

### `gbot run`

Start the API server (Uvicorn).

```bash
gbot run                          # default: 0.0.0.0:8000
gbot run --port 9000              # custom port
gbot run --host localhost         # localhost only
gbot run --reload                 # auto-reload on code changes
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--port` | `-p` | 8000 | Port number |
| `--host` | `-h` | 0.0.0.0 | Host address |
| `--reload` | | false | Enable auto-reload |

### `gbot chat`

Chat with the assistant. Connects to the API server by default.

```bash
gbot chat                         # interactive REPL
gbot chat -m "What's the weather?" # single message
gbot chat --local -m "Hello"      # local mode (no API server)
gbot chat --session abc123        # continue existing session
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--server` | `-s` | http://localhost:8000 | API server URL |
| `--token` | `-t` | (from credentials) | Bearer token |
| `--api-key` | `-k` | (from credentials) | API key |
| `--message` | `-m` | | Single message (non-interactive) |
| `--session` | | | Session ID to continue |
| `--local` | `-l` | false | Local standalone mode (no API) |

### `gbot login`

Authenticate and save credentials to `~/.graphbot/credentials.json`.

```bash
gbot login owner -p mypassword
gbot login alice --server http://remote:8000
```

| Argument | Description |
|----------|-------------|
| `USER_ID` | Username (required) |

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--server` | `-s` | http://localhost:8000 | API server URL |
| `--password` | `-p` | (prompted) | Password (hidden input) |

### `gbot logout`

Clear saved credentials.

```bash
gbot logout
```

### `gbot status`

Show comprehensive system stats with Rich panels: System, Context Layers, Tools, Data, Active Session.

```bash
gbot status                       # owner's latest session
gbot status -c telegram           # owner's telegram session
gbot status -u murat              # murat's latest session
gbot status -u murat -c telegram  # murat's telegram session
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--channel` | `-c` | | Filter session by channel (api, telegram) |
| `--user` | `-u` | owner | User ID for session/context info |

**Output panels:**

| Panel | Content |
|-------|---------|
| System | Version, model, thinking mode, token limit |
| Context Layers | Per-layer token/char usage with budget bars |
| Tools | Tool groups with names |
| Data | Users, sessions, tokens, messages, notes, cron, reminders |
| Active Session | Session ID, channel, messages, token progress bar |

### `gbot cron`

Manage cron jobs.

```bash
gbot cron list                    # list all cron jobs
gbot cron remove job_12345        # remove a cron job
```

### `gbot user`

Manage users.

```bash
# Add user
gbot user add ali --name "Ali" --password secret123
gbot user add zynp --name "Zeynep" --telegram "BOT_TOKEN"

# List users
gbot user list

# Remove user
gbot user remove ali

# Change password
gbot user set-password ali newpassword

# Link channel
gbot user link ali telegram 123456789
```

| Subcommand | Description |
|------------|-------------|
| `add <username>` | Create user (options: --name, --password, --telegram) |
| `list` | List all users with channels |
| `remove <username>` | Delete user |
| `set-password <user> <pass>` | Update password |
| `link <user> <channel> <id>` | Link channel identity |

---

## Interactive REPL

When you run `gbot` (or `gbot chat`), you enter the interactive REPL with:

- Auto-completion for slash commands
- Rich markdown rendering for responses
- Spinner animation while waiting
- Session management

### Slash Commands

Type `/` to see all available commands. Type `/help` for organized help panels.

#### Chat

| Command | Description |
|---------|-------------|
| `/history [n]` | Show last n messages (default: 10) |
| `/context` | Display user context (markdown) |
| `/clear` | Clear screen |

#### Session

| Command | Description |
|---------|-------------|
| `/session info` | Show current session ID |
| `/session new` | Start a new session |
| `/session list` | List all sessions |
| `/session end` | End current session |

#### Admin

| Command | Description |
|---------|-------------|
| `/status` | Server stats dashboard (System, Context, Overview, Current Session) |
| `/model` | Show active model name |
| `/config` | Show server configuration |
| `/user` | List all users |
| `/skill` | List available skills |
| `/cron [list\|remove <id>]` | Manage cron jobs |
| `/events` | Show pending system events |

#### Navigation

| Command | Description |
|---------|-------------|
| `/help` | Show help panels |
| `/exit` or `/quit` | Exit REPL |
| `/` | Show help (bare slash) |

---

## Credentials

Credentials are stored in `~/.graphbot/credentials.json` with 0600 permissions.

```json
{
  "server_url": "http://localhost:8000",
  "user_id": "owner",
  "token": "eyJ..."
}
```

- `gbot login` saves credentials
- `gbot logout` clears them
- `gbot chat` and `gbot` auto-load saved credentials

---

## Examples

### Daily workflow

```bash
# Start server (or use Docker)
gbot run &

# Login once
gbot login owner -p mypassword

# Chat interactively
gbot
> Hello, how are you?
> /status
> /session list
> /exit

# Check a specific user's telegram session
gbot status -u murat -c telegram

# Manage cron jobs
gbot cron list
gbot cron remove old_job_id

# Manage users
gbot user add newuser --name "New User" --password pass123
gbot user list
```

### Docker usage

```bash
# Start with Docker
docker compose up -d

# Use CLI commands against the container
docker compose exec graphbot gbot user add ali --name "Ali"
docker compose exec graphbot gbot cron list

# Or connect from host (if port exposed)
gbot login owner -p mypassword
gbot status
```
