"""``agent.delegation`` suite — DelegationPlanner LLM call quality.

Calls ``DelegationPlanner.plan(task)`` directly with a synthetic
``tool_catalog`` that mirrors production. Each case has expectations
on the ``execution`` type, ``processor`` choice, optional ``tool_name``
keywords, and optional ``cron_expr`` / ``delay_seconds`` constraints.

We don't need a working DB or runner for this — the planner only
reads ``config`` and the catalog string.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from gbot.agent.delegation import DelegationPlanner
from gbot.core.config.loader import load_config
from gbot_eval.suites import register
from gbot_eval.suites.base import CaseResult, SuiteResult, sample_cases

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

# Minimal tool catalog — production catalog is built from a registry,
# but for delegation eval we only need the planner to *see* a plausible
# set of tools. Mirrors gbot/agent/tools/registry.py groups.
_TOOL_CATALOG = """## Available Tools

### memory
- save_user_note(content): persist a note to long-term memory
- get_user_context(query): retrieve user context / past notes
- add_favorite(name, value): add a user favourite item
- search_memory(query): semantic search over the user's memory

### search
- web_search(query, count=5): search the web for real-time information
- web_fetch(url): fetch and summarize a URL
- get_current_time(): returns the current date and time

### filesystem
- read_file(path): read a local file
- write_file(path, content): write or overwrite a local file
- edit_file(path, old, new): apply a string replacement
- list_dir(path): list a directory's contents

### shell
- exec_command(command): run a shell command

### messaging
- send_message_to_user(text): proactively message the user

## Notes
- Use `agent` processor for multi-step or tool-using tasks.
- Use `static` for plain conversational replies that need no tool.
- Use `function` only for trivially deterministic single-tool calls.
"""


class AgentDelegationSuite:
    name = "agent.delegation"
    fixture_file = "agent_delegation"

    async def run(self, model: str, sample_pct: int = 100) -> SuiteResult:
        cases = sample_cases(self._load_cases(), sample_pct)

        cfg = load_config()
        # Override the planner model so we can A/B different models without
        # mutating the user's config.yaml.
        cfg.background.delegation.model = model
        planner = DelegationPlanner(cfg, _TOOL_CATALOG)

        case_results: list[CaseResult] = []
        for case in cases:
            start = time.monotonic()
            try:
                plan = await planner.plan(case["task"])
                error = None
            except Exception as e:
                logger.warning(f"agent.delegation {case['case_id']} crashed: {e}")
                plan = {}
                error = str(e)
            latency_ms = int((time.monotonic() - start) * 1000)

            quality, detail = _score(case, plan)

            # We don't get raw token counts back from the planner, so
            # tokens stay at 0 here — this is a single-call suite where
            # latency + quality are the headline signals.
            case_results.append(
                CaseResult(
                    case_id=case["case_id"],
                    quality=quality,
                    tokens_in=0,
                    tokens_out=0,
                    latency_ms=latency_ms,
                    cost_usd=0.0,
                    error=error,
                    detail={**detail, "plan": plan},
                )
            )

        return SuiteResult(
            name=self.name,
            model=model,
            ran_at=datetime.utcnow().isoformat(),
            cases=case_results,
            aggregate=_aggregate(case_results),
        )

    @staticmethod
    def _load_cases() -> list[dict]:
        return json.loads(
            (FIXTURES_DIR / "agent_delegation.json").read_text()
        )["cases"]


def _score(case: dict, plan: dict) -> tuple[float, dict]:
    """Score a delegation plan vs the case's expectations.

    Quality breakdown (each component ≤ 1, summed and normalised):
    - execution match (0/1)
    - processor membership (0/1)
    - tool_name keyword match (0/1) when expected
    - cron / delay constraint (0/1) when expected
    """
    parts: list[float] = []
    detail: dict[str, Any] = {}

    # Execution
    exec_actual = (plan.get("execution") or "").lower()
    if "expected_execution" in case:
        exp = case["expected_execution"].lower()
        parts.append(1.0 if exec_actual == exp else 0.0)
        detail["execution"] = {"expected": exp, "actual": exec_actual}
    elif "expected_execution_in" in case:
        opts = [e.lower() for e in case["expected_execution_in"]]
        parts.append(1.0 if exec_actual in opts else 0.0)
        detail["execution"] = {"expected_in": opts, "actual": exec_actual}

    # Processor
    proc_actual = (plan.get("processor") or "").lower()
    opts = [p.lower() for p in case.get("expected_processor_in", [])]
    if opts:
        parts.append(1.0 if proc_actual in opts else 0.0)
        detail["processor"] = {"expected_in": opts, "actual": proc_actual}

    # Tool name keyword
    if "expected_tool_keywords" in case:
        kws = [k.lower() for k in case["expected_tool_keywords"]]
        tool_name = (plan.get("tool_name") or "").lower()
        tool_list = [t.lower() for t in (plan.get("tools") or [])]
        haystack = " ".join([tool_name] + tool_list)
        hit = any(k in haystack for k in kws)
        parts.append(1.0 if hit else 0.0)
        detail["tool_keywords"] = {
            "expected_any": kws,
            "tool_name": tool_name,
            "tools": tool_list,
            "ok": hit,
        }

    # Multi-tool min count
    if "expected_tools_min" in case:
        n = len(plan.get("tools") or [])
        if not n and plan.get("tool_name"):
            n = 1
        ok = n >= case["expected_tools_min"]
        parts.append(1.0 if ok else 0.0)
        detail["tools_min"] = {"expected_min": case["expected_tools_min"], "got": n, "ok": ok}

    # Cron expression sanity
    if "expected_cron_substring" in case:
        cron = plan.get("cron_expr") or ""
        sub = case["expected_cron_substring"]
        ok = sub in cron
        parts.append(1.0 if ok else 0.0)
        detail["cron"] = {"expected_substring": sub, "actual": cron, "ok": ok}

    # Delay range
    if "expected_delay_seconds_min" in case and "expected_delay_seconds_max" in case:
        delay = plan.get("delay_seconds")
        if isinstance(delay, (int, float)):
            ok = case["expected_delay_seconds_min"] <= delay <= case["expected_delay_seconds_max"]
        else:
            ok = False
        parts.append(1.0 if ok else 0.0)
        detail["delay"] = {
            "expected_range": [case["expected_delay_seconds_min"], case["expected_delay_seconds_max"]],
            "actual": delay,
            "ok": ok,
        }

    quality = sum(parts) / len(parts) if parts else 0.0
    return quality, detail


def _aggregate(cases: list[CaseResult]) -> dict[str, Any]:
    if not cases:
        return {}
    qualities = [c.quality for c in cases]
    latencies = sorted(c.latency_ms for c in cases)
    p95_idx = max(0, int(len(latencies) * 0.95) - 1)
    return {
        "quality": sum(qualities) / len(qualities),
        "tokens_in_avg": 0,
        "tokens_out_avg": 0,
        "tokens_total": 0,
        "latency_ms_p50": latencies[len(latencies) // 2],
        "latency_ms_p95": latencies[p95_idx],
        "cost_total_usd": 0.0,
        "cases_total": len(cases),
        "failures": sum(1 for c in cases if c.error),
    }


register(AgentDelegationSuite())
