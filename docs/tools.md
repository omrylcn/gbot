# Tools Reference

GBot has 23+ tools organized into 9 groups. Tool access is controlled by RBAC roles via `config/roles.yaml`.

---

## Tool Groups & Role Access

| Group | Tools | Owner | Member | Guest |
|-------|-------|:-----:|:------:|:-----:|
| **memory** | 8 tools | yes | yes | — |
| **search** | 3 tools | yes | yes | — |
| **web** | 2 tools | yes | yes | yes |
| **filesystem** | 4 tools | yes | — | — |
| **shell** | 1 tool | yes | — | — |
| **messaging** | 1 tool | yes | yes | — |
| **scheduling** | 7 tools | yes | yes | — |
| **delegation** | 3 tools | yes | yes | — |
| **skills** | 1 tool | yes | yes | — |

---

## memory — User Data Management

| Tool | Parameters | Description |
|------|-----------|-------------|
| `save_user_note` | `user_id`, `note` | Save a learned fact or note about the user |
| `get_user_context` | `user_id` | Get full user context (notes, favorites, preferences, activities) |
| `add_favorite` | `user_id`, `item_id`, `item_title` | Add item to favorites |
| `get_favorites` | `user_id` | Get user's favorite items |
| `remove_favorite` | `user_id`, `item_id` | Remove item from favorites |
| `set_user_preference` | `user_id`, `key`, `value` | Save a preference (e.g., language, theme) |
| `get_user_preferences` | `user_id` | Get all preferences |
| `remove_user_preference` | `user_id`, `key` | Remove a preference |

---

## search — Knowledge Base & Time

| Tool | Parameters | Description |
|------|-----------|-------------|
| `search_items` | `query`, `max_results=5` | Search the RAG knowledge base (requires RAG config) |
| `get_item_detail` | `item_id` | Get detailed info about a specific item |
| `get_current_time` | `timezone_name="Europe/Istanbul"` | Get current date, time, and day of week |

---

## web — Internet Access

| Tool | Parameters | Description |
|------|-----------|-------------|
| `web_search` | `query`, `count=5` | Search the web (DuckDuckGo → Tavily → Moonshot fallback chain) |
| `web_fetch` | `url`, `max_chars=50000` | Fetch a URL or shortcut tag and return text content |

**Shortcuts:** `web_fetch("gold")` uses shortcuts defined in `config.yaml` → `tools.web.fetch_shortcuts`.

---

## filesystem — Workspace File Operations

| Tool | Parameters | Description |
|------|-----------|-------------|
| `read_file` | `path` | Read a text file from workspace |
| `write_file` | `path`, `content` | Write content to a file (creates dirs if needed) |
| `edit_file` | `path`, `old_text`, `new_text` | Replace exact text in a file |
| `list_dir` | `path="."` | List directory contents |

All paths are relative to the workspace directory (`assistant.workspace` in config).

---

## shell — Command Execution

| Tool | Parameters | Description |
|------|-----------|-------------|
| `exec_command` | `command`, `working_dir=null` | Execute a shell command |

**Safety:** Dangerous commands (`rm -rf`, `format`, `mkfs`, etc.) are blocked. Timeout configurable via `tools.shell.timeout`.

---

## messaging — Cross-User Communication

| Tool | Parameters | Description |
|------|-----------|-------------|
| `send_message_to_user` | `target_user`, `message`, `channel="telegram"` | Send a message to another user via Telegram or WhatsApp |

Routing: finds user → checks channel link → delivers. Fallback: WhatsApp → Telegram if specified channel has no link.

In background mode (cron/reminder), messages get `[gbot]` prefix automatically.

---

## scheduling — Reminders, Cron Jobs & Alerts

| Tool | Parameters | Description |
|------|-----------|-------------|
| `create_reminder` | `user_id`, `delay_seconds`, `message`, `channel`, `agent_prompt?`, `agent_tools?` | One-shot reminder (static or agent mode) |
| `list_reminders` | `user_id` | List pending reminders |
| `cancel_reminder` | `reminder_id` | Cancel a pending reminder |
| `add_cron_job` | `user_id`, `cron_expr`, `message`, `channel`, `agent_prompt?`, `agent_tools?` | Recurring task with cron expression |
| `list_cron_jobs` | `user_id` | List scheduled cron jobs |
| `remove_cron_job` | `job_id` | Remove a cron job |
| `create_alert` | `user_id`, `cron_expr`, `check_message`, `channel`, `agent_tools?` | Monitoring alert — notifies only when condition is met |

See [scheduling.md](scheduling.md) for detailed usage and decision tree.

---

## delegation — Background Task Management

| Tool | Parameters | Description |
|------|-----------|-------------|
| `delegate` | `user_id`, `task`, `channel="api"` | Delegate a task for background/scheduled execution |
| `list_scheduled_tasks` | `user_id` | List all scheduled tasks (crons, reminders, background) |
| `cancel_scheduled_task` | `task_id` | Cancel a task (`cron:id`, `reminder:id`, or raw ID) |

The `delegate` tool uses a planner LLM to decide the best execution strategy (immediate background, delayed, or scheduled).

---

## skills — Progressive Disclosure

| Tool | Parameters | Description |
|------|-----------|-------------|
| `load_skill` | `skill_name` | Load detailed instructions for a specific skill |

Skills are markdown files (`SKILL.md`) that provide domain-specific instructions. "Always-on" skills are loaded into context automatically; others are loaded on-demand via this tool to save tokens.

---

## Background Agent Tools

When tools run in background mode (cron jobs, reminders, delegation), only a subset is available:

| Allowed | Not Allowed |
|---------|-------------|
| `send_message_to_user` | `exec_command` |
| `web_search`, `web_fetch` | `read_file`, `write_file`, `edit_file`, `list_dir` |
| `save_user_note` (memory write) | `delegate` (no recursive delegation) |
| `search_items` | Scheduling tools (no recursive scheduling) |

This is a security boundary — background agents cannot access the filesystem, shell, or create new scheduled tasks.
