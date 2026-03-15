# Scheduling System — Reminders, Cron Jobs & Alerts

> Version: v1.14.0 | Last updated: 2026-03-15

---

## 1. Overview

GBot provides **3 scheduling tools**. All use the same infrastructure (APScheduler) but serve different use cases:

| Tool | When | Repeat | Notification | Mode |
|------|------|--------|--------------|------|
| `create_reminder` | X seconds later | One-time | Always | Static or Agent |
| `add_cron_job` | Cron expression | Recurring | Always | Static or Agent |
| `create_alert` | Cron expression | Recurring | **Only if condition met** | Agent only |

---

## 2. Tool Details

### 2.1 create_reminder — One-Time Reminder

**Usage:** "Do X in N minutes/hours"

**Two modes:**

**Static mode** — Message is delivered as-is, no LLM call:
```
User: "Remind me about the meeting in 2 hours"
→ create_reminder(delay_seconds=7200, message="Meeting reminder!")
→ 2 hours later → user receives "Meeting reminder!"
```

**Agent mode** — LightAgent runs, can use tools:
```
User: "Send 'hello' to Murat in 5 minutes"
→ create_reminder(
    delay_seconds=300,
    message="Send 'hello' to Murat",
    agent_prompt="Use send_message_to_user tool to send 'hello' to user Murat.",
    agent_tools=["send_message_to_user"]
  )
→ 5 min later → LightAgent → send_message_to_user("Murat", "hello")
```

**Decision logic:** If `agent_prompt` is set → Agent mode, otherwise → Static mode.

---

### 2.2 add_cron_job — Recurring Task

**Usage:** "Do X every day/hour/minute"

Uses cron expressions (APScheduler CronTrigger):
```
*/10 * * * *     → Every 10 minutes
0 9 * * *        → Every day at 09:00
0 9 * * 1-5      → Weekdays at 09:00
0 */2 * * *      → Every 2 hours
```

**Examples:**

Static:
```
User: "Say 'good morning' every day at 9"
→ add_cron_job(cron_expr="0 9 * * *", message="Good morning!")
→ Every day 09:00 → "Good morning!" is sent
```

Agent:
```
User: "Send a greeting to Murat every 10 minutes"
→ add_cron_job(
    cron_expr="*/10 * * * *",
    message="Send greeting to Murat",
    agent_prompt="Send 'hello' to user Murat using send_message_to_user tool.",
    agent_tools=["send_message_to_user"]
  )
→ Every 10 min → LightAgent → sends "hello" to Murat
```

---

### 2.3 create_alert — Smart Monitoring

**Usage:** "Monitor X, notify me if something important happens"

`create_alert` = `add_cron_job` + **NOTIFY/SKIP mechanism**

On each trigger, LightAgent runs and evaluates:
- Condition met → User receives notification (NOTIFY)
- Condition not met → Silently skipped ([SKIP])

```
User: "Check gold price every 30 min, notify if above 7500"
→ create_alert(
    cron_expr="*/30 * * * *",
    check_message="Use web_fetch to check gold prices. If gram gold is above 7500 TL, notify. Otherwise respond with [SKIP]."
  )
→ Every 30 min → LightAgent:
    1. web_fetch("gold") → get price data
    2. Price < 7500 → "[SKIP]" → NO notification sent
    3. Price >= 7500 → "Gold is at 7523 TL!" → notification SENT
```

**IMPORTANT:** `check_message` is a **task instruction**, not a result message!
- Wrong: `check_message="Gold exceeded 7500!"` (result text)
- Correct: `check_message="Check gold price, notify if above 7500"` (task instruction)

---

## 3. Architecture — How It Works

```
User message
    │
    ▼
MainAgent (GraphRunner)
    │
    ├─ create_reminder() ──→ APScheduler DateTrigger
    ├─ add_cron_job()     ──→ APScheduler CronTrigger
    └─ create_alert()     ──→ APScheduler CronTrigger + NOTIFY/SKIP
                                    │
                                    ▼
                              Trigger time
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
              Static mode      Agent mode       Alert (Agent)
              (send message)   (LightAgent)    (LightAgent + SKIP)
                    │               │               │
                    ▼               ▼               ▼
              Telegram/API    Run tools         Check condition
                              → send result     → SKIP or notify
```

### 3.1 Infrastructure Components

| Component | File | Purpose |
|-----------|------|---------|
| CronScheduler | `gbot/core/cron/scheduler.py` | APScheduler management, job/reminder CRUD |
| LightAgent | `gbot/agent/light.py` | Isolated agent — own graph, restricted tool set |
| Background Registry | `gbot/agent/tools/registry.py` | Tools available in agent mode |
| Tool: cron_tool.py | `gbot/agent/tools/cron_tool.py` | add_cron_job, list_cron_jobs, remove_cron_job, create_alert |
| Tool: reminder.py | `gbot/agent/tools/reminder.py` | create_reminder, list_reminders, cancel_reminder |

### 3.2 Database Tables

**cron_jobs** — Recurring tasks:
```
job_id, user_id, cron_expr, message, channel, enabled,
agent_prompt, agent_tools, agent_model, notify_condition,
consecutive_failures, created_at
```

**reminders** — One-time reminders:
```
reminder_id, user_id, channel, message, run_at, status,
agent_prompt, agent_tools, cron_expr, created_at
```

**cron_execution_log** — Every execution is logged:
```
log_id, job_id, result, status (success/error/skipped), duration_ms, executed_at
```

### 3.3 Error Handling

- **3 consecutive failures** → Job automatically set to `paused` (`consecutive_failures >= 3`)
- Successful execution → `consecutive_failures` resets to zero
- Execution log is written for every run (success/error/skip)

---

## 4. Agent Mode — Available Tools

Background agent (LightAgent) can access these tools:

| Tool | Description |
|------|-------------|
| `send_message_to_user` | Send a message to another user |
| `web_search` | Search the web |
| `web_fetch` | Fetch data from URL or shortcut (gold, weather, etc.) |
| `save_memory` | Save to agent memory |
| `search_memory` | Search agent memory |

**Security:** `filesystem`, `shell`, `delegation`, and `scheduling` tool groups are **disabled** for background agents.

---

## 5. Decision Tree — Which Tool Should I Use?

```
What does the user want?
    │
    ├─ One-time?
    │   └─ create_reminder
    │       ├─ Simple reminder → Static (agent_prompt=None)
    │       └─ Action needed → Agent (agent_prompt + agent_tools)
    │
    └─ Recurring?
        ├─ Always notify → add_cron_job
        │   ├─ Simple message → Static
        │   └─ Action needed → Agent
        │
        └─ Conditional notify → create_alert
            └─ check_message = task instruction
```

---

## 6. Known Limitations

| Limitation | Description | Workaround |
|------------|-------------|------------|
| Scheduler cache | Deleted cron jobs not removed from APScheduler until restart | Container restart or `reload()` (TODO) |
| Timezone | Uses container timezone (`TZ` env var, default: `Europe/Istanbul`) | Set `TZ` in docker-compose.yml |
| Min interval | APScheduler minimum ~1 second | Practical limit: 1 minute |
| Tool access | Background agent cannot access all tools | Restricted by design for security |
