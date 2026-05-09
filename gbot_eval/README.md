# gbot-eval — LLM evaluation CLI

**Single-command quality / cost / latency benchmark for every LLM call
GBot makes.** Memory pipeline (extraction, AUDN, entity-page compile),
agent surface (delegation, tool calling, structured output, instruction
following), and a stub long-context stress test — all in one CLI.

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

# Single suite
gbot-eval run --suite=memory.extraction
gbot-eval run --suite=agent       # group filter — runs all agent.*

# See what's registered
gbot-eval list

# Inspect known model pricing
gbot-eval models

# After a run, view the matrix
gbot-eval matrix

# Compare two runs
gbot-eval list-runs
gbot-eval compare <ts_a> <ts_b>

# Set a baseline + diff future runs against it
gbot-eval baseline set --run=<ts>
gbot-eval baseline                 # show current baseline
gbot-eval baseline diff            # latest run vs baseline

# Clean disk
gbot-eval clean --keep=10
```

## What each suite measures

| Suite | What | Mean quality (gemini-3-flash baseline) |
|---|---|---|
| `memory.extraction` | Fact extraction recall + relation recall + category accuracy | 0.80 |
| `memory.audn` | ADD/UPDATE/DELETE/NOOP decision accuracy | 0.93 |
| `memory.page_compile` | Entity-page citation + keyword + hallucination + format | 0.00 † |
| `agent.delegation` | DelegationPlanner: execution / processor / tool / cron / delay | 0.98 |
| `agent.tool_calling` | Tool name / args / no-tool adherence | 0.95 |
| `agent.structured` | JSON schema: keys, types, arrays, nesting, enums | 1.00 |
| `agent.instruction` | 10 regex + 5 LLM-as-judge — language, tone, format | 0.73 |
| `stress.long_context` | 30-turn needle-in-haystack, early/middle/late position | 1.00 |

† gemini-3-flash never emits the `[fact_id:xxx]` citation format the
production prompt requires. Production has `entity_pages.enabled=false`
by default, so this is a known known.

## Configuration

`gbot-eval` reads gbot's `config/config.yaml` for the default model
(`memory.model` → `assistant.model`). Override per-run with `--model=`.

Provider: OpenRouter SDK is used directly (production parity). LiteLLM
fallback exists in gbot core but isn't exercised — confirmed by the
`bench-providers` head-to-head (see `bench_providers.py`); both
providers tied on quality and cost, OpenRouter SDK was equal or better
on latency. Decision: OpenRouter SDK is the sole provider going forward.

API keys: needs at least one of `OPENROUTER_API_KEY` (typical) or
`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY`.

## Output

Every run writes to `gbot_eval/output/runs/<ts>_<model>/`:

```
manifest.json              — model, ran_at, totals, suite list
matrix.json                — cross-suite aggregate
memory_extraction.json     — per-suite (one file each)
memory_audn.json
...
```

Run dirs are gitignored. `clean --keep=N` prunes old ones.

## Adding a model price

Per-call cost requires the model in the pricing table. Built-in
entries cover the common providers (Gemini, Claude, GPT, Kimi,
DeepSeek, MiniMax, GLM). For new models:

```bash
gbot-eval models add openrouter/foo/bar --prompt=0.50 --completion=2.00
```

Lands in `gbot_eval/output/pricing_overrides.json` (gitignored). The
in-tree `pricing.py` table stays clean.

## Reasoning models — a gotcha

For Kimi-K2 family, DeepSeek-R1, etc., the suites pass
`reasoning={"effort": "none"}` to disable thinking. **Important:**
`{"enabled": false}` does NOT work — OpenRouter API silently dismisses
it. Verified empirically (see `bench_providers.py:_is_reasoning_model`).

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
8 suites, ~85 cases, 56K tokens, ~$0.012, ~4 min wall-clock
```

LLM-as-judge cases in `agent.instruction` use a fixed judge model
(`claude-haiku-4.5`) so the budget is dominated by them — without
those, a full run costs ~$0.005.

## Source layout

```
gbot_eval/
├── cli.py                 — Typer entry (gbot-eval)
├── config.py              — provider setup, model resolution
├── pricing.py             — model → $/1M token table + overrides
├── capture.py             — track_call: token / latency / cost
├── judge.py               — LLM-as-judge helper
├── reporting.py           — Rich tables, matrix aggregator
├── runner.py              — suite orchestrator, output writer
├── bench_providers.py     — LiteLLM vs OpenRouter SDK head-to-head
├── suites/
│   ├── base.py            — Suite Protocol, CaseResult/SuiteResult
│   ├── _metrics.py        — extraction/AUDN/page metrics
│   ├── _memory_helpers.py — fixture loader, MemoryService bootstrap
│   ├── memory_*.py        — 3 memory suites
│   ├── agent_*.py         — 4 agent suites
│   └── stress_long_context.py
├── fixtures/              — JSON test cases (in tree)
└── output/                — run dirs + baseline + overrides (gitignored)
```

See `ASSESSMENT.md` for the design rationale and known debt.
