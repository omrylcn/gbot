---
name: scheduling
description: Scheduling decision tree — delegate tool handles all scheduling
always: false
metadata:
  requires: {}
---

# Scheduling — Decision Tree

All scheduling goes through the `delegate` tool. The DelegationPlanner decides execution type and processor automatically.

## Examples

**One-shot reminder:**
- "2 saat sonra toplantıyı hatırlat" → `delegate(task="2 saat sonra toplantı hatırlatması gönder")`

**Recurring job:**
- "Her sabah 9'da günaydın de" → `delegate(task="Her sabah 9'da günaydın mesajı gönder")`

**Monitoring alert:**
- "Altın 7500'ü geçerse bildir" → `delegate(task="Her 30 dk altın fiyatını kontrol et, 7500 TL üstüyse bildir")`

## How it works

The `delegate` tool sends your task to the DelegationPlanner which picks:
- **Execution type**: immediate / delayed / recurring / monitor
- **Processor**: static (plain text) / function (tool call) / agent (LightAgent)

You do NOT need to specify cron expressions, delay seconds, or processor types. Just describe what you want in natural language.

## Listing & Cancelling

- `list_scheduled_tasks(user_id)` — shows active tasks
- `cancel_scheduled_task(task_id)` — cancels a task
