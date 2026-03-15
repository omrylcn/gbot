# Context System

GBot builds a system prompt from multiple layers before each LLM call. This layered approach allows fine-grained control over what the agent knows and how it behaves.

---

## Layers

The context is assembled from 8+ layers in this order:

| # | Layer | Source | Description |
|---|-------|--------|-------------|
| 1 | **identity** | `workspace/AGENT.md` or `system_prompt` config | Bot personality, rules, behavior guidelines |
| 2 | **runtime** | Config + system clock | Current user, datetime, model info |
| 3 | **role** | `config/roles.yaml` | RBAC role description for current user |
| 4 | **agent_memory** | `agent_memory` table | Long-term facts the bot has learned across sessions |
| 5 | **user_context** | `user_notes` + `preferences` + `favorites` | User-specific notes, settings, and saved items |
| 6 | **events** | `system_events` table | Undelivered background notifications (cron results, reminders) |
| 7 | **session_summary** | `sessions` table | Summary of previous session (LLM-generated) |
| 8 | **skills** | `SKILL.md` files | Always-on skill instructions |
| 9 | **skills_index** | `SKILL.md` file listing | Available skills for `load_skill()` tool |

---

## How It Works

```
User sends message
    │
    ▼
ContextBuilder.build(user_id, role)
    │
    ├─ Layer 1: Read AGENT.md → identity
    ├─ Layer 2: Inject user, date, model → runtime
    ├─ Layer 3: Lookup role description → role
    ├─ Layer 4: Query agent_memory table → agent_memory
    ├─ Layer 5: Query notes + prefs + favs → user_context
    ├─ Layer 6: Get undelivered events → events
    ├─ Layer 7: Get previous session summary → session_summary
    ├─ Layer 8: Load always-on SKILL.md files → skills
    └─ Layer 9: List available skills → skills_index
    │
    ▼
Concatenated system prompt → LLM
```

---

## RBAC Filtering

Not all users see all layers. Roles define which layers are visible:

| Layer | Owner | Member | Guest |
|-------|:-----:|:------:|:-----:|
| identity | yes | yes | yes |
| runtime | yes | yes | yes |
| role | yes | yes | yes |
| agent_memory | yes | yes | — |
| user_context | yes | yes | — |
| events | yes | yes | — |
| session_summary | yes | yes | — |
| skills | yes | yes | — |

Guest users get a minimal context: just identity, runtime, and role.

---

## Token Budget

Each layer has an approximate token budget to prevent context overflow:

| Layer | Default Budget |
|-------|---------------|
| identity | 500 tokens |
| agent_memory | 500 tokens |
| user_context | 1500 tokens |
| session_summary | 500 tokens |
| skills | 1000 tokens |

Configure via `assistant.context_priorities` in config:

```yaml
assistant:
  context_priorities:
    identity: 500
    agent_memory: 500
    user_context: 1500
    session_summary: 500
    skills: 1000
```

---

## Agent Profiles

Three agent types have different context compositions:

### Main Agent
Full 8-layer context. Used for interactive conversations.

### Planner (Delegation)
Minimal context: identity + runtime + task description. Used by `DelegationPlanner` to decide execution strategy for delegated tasks.

### Light Agent (Background)
Reduced context: identity + runtime + task-specific instructions. Used by cron jobs, reminders, and background tasks. No user_context or session_summary.

Profile definitions live in `config/agents.yaml`:

```yaml
profiles:
  main:
    agent_md: "workspace/AGENT.md"
    skills: ["scheduling", "weather"]
  planner:
    agent_md: "workspace/agents/planner/AGENT.md"
  light:
    agent_md: "workspace/agents/light/AGENT.md"
```

---

## Runtime Overrides

Owners can override any layer at runtime via the admin API — useful for testing prompt changes without restarting.

```bash
# Override identity layer for main agent
curl -X PUT http://localhost:8000/admin/context/main/layers/identity \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"content": "You are a pirate assistant. Arrr!", "enabled": true}'

# Preview the result
curl http://localhost:8000/admin/context/main/preview \
  -H "Authorization: Bearer <token>"

# Clear override
curl -X DELETE http://localhost:8000/admin/context/main/layers/identity \
  -H "Authorization: Bearer <token>"

# Clear all overrides
curl -X DELETE http://localhost:8000/admin/context/overrides \
  -H "Authorization: Bearer <token>"
```

Overrides are stored in memory — they reset on server restart.

---

## Dashboard Context Inspector

The admin dashboard (port 3001) provides a visual context inspector:

1. **Layers tab** — Table showing each layer's name, char count, token count, enabled status
2. **Override** — Edit layer content with a textarea, toggle enabled/disabled
3. **Preview** — Full rendered context string as the LLM sees it

Navigate to: Dashboard → Context → Select profile (main/planner/light)

---

## Key Files

| File | Description |
|------|-------------|
| `gbot/agent/context/builder.py` | ContextBuilder — assembles layers |
| `gbot/agent/context/models.py` | LayerResult, ContextBudget models |
| `gbot/agent/context/service.py` | ContextService — facade for admin API |
| `gbot/agent/profiles.py` | Agent profiles loader (agents.yaml) |
| `config/agents.yaml` | Profile definitions |
| `config/roles.yaml` | RBAC layer visibility |
