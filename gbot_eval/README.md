# gbot-eval — LLM evaluation CLI

**Single-command quality / cost / latency benchmark for every LLM call
GBot makes.** Memory pipeline (extraction, AUDN, entity-page compile),
agent surface (delegation, tool calling, structured output, instruction
following, multi-turn coherence), and a stub long-context stress test —
all in one CLI.

Used to:
- Vet a new model before swapping it into production (`--model=<id>`)
- Catch regressions when an upstream model changes
- Compare gbot's LLM quality across versions (`baseline` workflow)

## Quick start

```bash
# Full run on the configured production model
gbot-eval run

# Try a different model
gbot-eval run --model=openrouter/moonshotai/kimi-k2.6

# Quick smoke (first 20% of every fixture)
gbot-eval run --sample=20

# Single suite or a group filter
gbot-eval run --suite=memory.extraction
gbot-eval run --suite=agent       # group filter — runs all agent.*

# See what's registered
gbot-eval list

# Inspect known model pricing — refresh from OpenRouter
gbot-eval models                  # show table
gbot-eval models refresh          # pull live prices from OpenRouter API
gbot-eval models add openrouter/foo/bar --prompt=0.5 --completion=2.0

# After a run, view the matrix
gbot-eval matrix

# Compare runs
gbot-eval list-runs
gbot-eval compare <ts_a> <ts_b>

# Set / diff a baseline
gbot-eval baseline set --run=<ts>
gbot-eval baseline                 # current
gbot-eval baseline diff            # latest run vs baseline

# Disk hygiene
gbot-eval clean --keep=10
```

## What each suite measures

| Suite | What | Default quality (gemini-3-flash baseline) |
|---|---|---|
| `general` | Factual recall, arithmetic, short-form generation | 1.00 |
| `agent.tool_calling` | Tool name / args / no-tool adherence (Turkish-fold) | 1.00 |
| `agent.structured` | JSON schema: keys, types, arrays, nesting, enums | 1.00 |
| `agent.instruction` | 10 deterministic + 5 LLM-as-judge — language, tone, format | 0.73 |
| `agent.delegation` | DelegationPlanner: execution / processor / tool / cron / delay | 0.98 |
| `agent.multi_turn` | Multi-turn dialog coherence (3-turn cases) | 1.00 |
| `memory.extraction` | Fact extraction recall + relation recall + category accuracy | 0.80 |
| `memory.audn` | ADD/UPDATE/DELETE/NOOP decision accuracy | 0.93 |
| `memory.page_compile` | Composite: 40% keyword + 30% no-hallu + 15% format + 15% citation | ~0.85 |
| `stress.long_context` | 30-turn needle-in-haystack at early / middle / late position | 1.00 |

## Architecture

```
gbot_eval/
├── cli.py                    Typer entry — every subcommand
├── runner.py                 Orchestrator: run_all → write_run
├── config.py                 Bootstrap: provider init, model resolution
├── pricing.py                Static $/1M table + overrides + live refresh
├── capture.py                track_call: tokens / latency / cost capture
├── judge.py                  LLM-as-judge (used by scoring/judge.py)
├── reporting.py              Rich tables, matrix aggregator
├── runners/                  Per-suite execution glue
│   ├── chat_completion.py    Generic — used by every YAML-only suite
│   ├── stress_long_context.py  Builds 30-turn dialogs
│   ├── multi_turn.py         Threads conversation history
│   └── memory_*.py + delegation.py  gbot-bound (soft import-guard)
├── scoring/                  Declarative scoring DSL
│   ├── builtins.py           17 built-in `kind`s (regex, json, tools, ...)
│   ├── judge.py              `kind: judge` LLM-as-judge rule
│   ├── expr.py               `kind: python` restricted Python escape hatch
│   └── memory_metrics.py     Helpers for the gbot-bound runners
├── suites/                   YAML files only (one per suite)
│   ├── general.yaml
│   ├── agent.*.yaml          (5 suites)
│   ├── memory.*.yaml         (3 suites — requires_gbot: true)
│   └── stress.long_context.yaml
├── catalogs/                 Shared reference data
│   └── standard_tools.yaml   Tool definitions used by tool_calling
├── output/                   Run artefacts (gitignored)
│   └── runs/<ts>_<model>/    manifest.json + matrix.json + per-suite JSON
└── README.md
```

Three-layer flow:

1. **CLI** parses `--model` / `--suite` / `--sample`, calls `runner.run_all`.
2. **Runner** loads YAML suites from `suites/*.yaml`, dispatches each
   case to the named **runner** (`chat_completion`, `delegation`, etc.).
3. **Scoring** rules from each YAML case (`scoring: [{kind, ...}]`)
   resolve through `SCORING_REGISTRY` and produce `[0,1]` scores;
   case quality is mean of rule scores.

## Reading a suite YAML

A minimal suite:

```yaml
name: general
description: |
  Quick factual / reasoning / format smoke checks.
runner: chat_completion        # which Python runner handles each case
requires_gbot: false           # skip if gbot isn't installed
default_max_tokens: 200
default_temperature: 0.1

cases:
  - id: gen01
    description: Factual recall
    task: "Türkiye'nin başkenti?"          # shorthand for messages
    max_tokens: 50
    scoring:
      - {kind: substring_any, values: [ankara], fold: turkish}
```

A case with explicit messages + JSON schema:

```yaml
- id: st01
  messages:
    - {role: system, content: "Return ONLY JSON."}
    - {role: user, content: "Ahmet 25 yaşında. JSON: {name, age}"}
  scoring:
    - {kind: json_valid}
    - {kind: json_keys, keys: [name, age]}
    - {kind: json_types, types: {age: int}}
```

A case with tool calling:

```yaml
- id: tc01
  task: "İstanbul hava durumu öğren."
  available_tools: [web_search]      # name from catalogs/standard_tools.yaml
  scoring:
    - {kind: tool_called, expected: web_search}
    - {kind: required_args, tool: web_search, keys: [query]}
    - kind: arg_substring_any
      tool: web_search
      arg: query
      values: [istanbul, weather]
      fold: turkish
```

A case with LLM-as-judge:

```yaml
- id: if11
  messages:
    - {role: system, content: "Profesyonel ton kullan."}
    - {role: user, content: "Zam talep e-postası yaz."}
  scoring:
    - kind: judge
      criteria: "Yanıt profesyonel ve resmi tonda mı?"
      min_score: 4
```

A case with the Python escape hatch:

```yaml
- id: edge_case
  task: "..."
  scoring:
    - kind: python
      expr: |
        # Locals: text, tool_calls, case, call (CallResult)
        d = json.loads(text) if text.strip() else {}
        ok = isinstance(d.get("items"), list) and len(d["items"]) >= 3
        return {"score": 1.0 if ok else 0.0, "detail": {"got": d.get("items")}}
```

### Scoring kinds (built-in)

| Kind | Purpose | Key params |
|---|---|---|
| `regex_match` / `regex_not_match` | Pattern check | `pattern`, optional `flags` |
| `substring_any` / `_all` / `_none` | Substring check | `values`, optional `fold` |
| `tool_called` / `tool_not_called` / `no_tool_call` | Tool selection | `expected` / `forbidden` |
| `tool_count_min` | Multi-tool min | `min` |
| `required_args` | Tool arg presence | `tool`, `keys` |
| `arg_substring_any` | Tool arg value | `tool`, `arg`, `values`, optional `fold` |
| `json_valid` / `_keys` / `_types` / `_array_min` / `_nested_keys` | JSON schema | rule-specific |
| `bullet_count` / `numbered_list` | Formatted output | `min` / `max` / `exact` |
| `word_count` / `sentence_count` | Length bound | `min` / `max` |
| `judge` | LLM-as-judge | `criteria`, optional `min_score`, `judge_model` |
| `python` | Restricted Python escape hatch | `expr` |

`fold: turkish` lowercases AND maps Turkish letters to ASCII so
`İstanbul`, `istanbul`, and `istanbul` all match.

## Configuration

Reads gbot's `config/config.yaml` for the default model
(`memory.model` → `assistant.model`). Override per-run with `--model=`.

Provider: OpenRouter SDK exclusively (decided 2026-05-09 in v1.21.1
via the empirical `bench-providers` decision).

API keys: `OPENROUTER_API_KEY` is required. Read from `.env` via
`load_dotenv()` at config-load time.

## Output

Every run writes to `gbot_eval/output/runs/<ts>_<model>/`:

```
manifest.json              — model, ran_at, sample_pct, totals
matrix.json                — cross-suite aggregate
agent_delegation.json      — per-suite (one file each)
memory_audn.json
...
```

Run dirs are gitignored. `clean --keep=N` prunes old ones.

## Standalone vs gbot-bound suites

Suite YAMLs declare `requires_gbot: true|false`. When gbot isn't
installed in the current environment, gbot-bound suites are skipped
at load time:

```
Standalone suites (no gbot required):
  • general
  • agent.{instruction,multi_turn,structured,tool_calling}
  • stress.long_context

gbot-bound suites:
  • agent.delegation
  • memory.{audn,extraction,page_compile}
```

## Reasoning models — a gotcha

For Kimi-K2 family, DeepSeek-R1, etc., disable thinking with
`reasoning={"effort": "none"}`. **Important:** `{"enabled": false}`
does NOT work — OpenRouter API silently dismisses it.

If a model wraps its answer in reasoning prose anyway,
`CallResult.text` falls back to `additional_kwargs.reasoning_content`
— mirrors the production fallback in `gbot/agent/delegation.py:214`.

## Sampling for cost-saving smoke tests

```bash
gbot-eval run --sample=20   # first 20% of each fixture
```

Deterministic prefix slice (not random), so two runs with the same
sample size are directly comparable. The percent is recorded in
`manifest.json` so cross-run comparisons can refuse mismatched samples
later.

## Cost ballpark (full run, gemini-3-flash baseline)

```
10 suites, ~110 cases, ~70K tokens, ~$0.015, ~5 min wall-clock
```

LLM-as-judge cases in `agent.instruction` use a fixed judge model
(`claude-haiku-4.5`); without those, a full run costs ~$0.005.

## Known limitations

1. **Quality definitions vary across suites** — most use mean-of-rules,
   `memory.page_compile` uses a weighted composite. Each suite's
   YAML `description` documents its own quality formula.
2. **Reasoning model auto-handling not yet wired into runners** —
   `bench_providers` had this, removed in 5K. Re-introduction is
   tracked as Step 6K (capture.py-level toggle).
3. **Cost is approximate** — uses a static / refreshed pricing table;
   actual OpenRouter charge can drift ±5% from provider routing
   variation.
4. **Long-context is stub-mode** — measures the raw model's attention,
   not the production `ContextBuilder` pipeline. The latter is
   covered by regression tests in `tests/test_*.py`.
5. **Restricted Python eval (`kind: python`)** — AST-validates against
   imports, dunder probing, and dangerous builtins, but is NOT a
   sandbox. Trust your YAMLs (lokal dev tool assumption).
