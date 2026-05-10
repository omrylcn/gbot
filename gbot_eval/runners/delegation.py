"""``delegation`` runner — gbot-bound.

Calls ``DelegationPlanner.plan(task)`` directly and scores the
returned plan against per-case expectations: execution type,
processor choice, tool keyword hit, cron / delay constraints.

Tokens / cost stay at 0 because the production planner doesn't
return usage from its facade call. Latency is the headline signal
along with quality.
"""

from __future__ import annotations

import time
from typing import Any

try:
    from gbot.agent.delegation import DelegationPlanner
    from gbot.core.config.loader import load_config
    _GBOT_AVAILABLE = True
except ImportError:
    DelegationPlanner = None  # type: ignore
    load_config = None  # type: ignore
    _GBOT_AVAILABLE = False

from loguru import logger

from gbot_eval import pricing
from gbot_eval.runners import register
from gbot_eval.runners.base import Runner
from gbot_eval.suites.base import CaseResult

# Minimal tool catalog so the planner sees a plausible toolset
# without requiring a live ToolRegistry. Mirrors the gbot
# production groups.
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


@register("delegation")
class DelegationRunner(Runner):
    name = "delegation"
    _planner_cache: dict[str, DelegationPlanner] = {}

    async def run_case(
        self,
        case: dict,
        suite_config: dict,
        model: str,
    ) -> CaseResult:
        if not _GBOT_AVAILABLE:
            raise RuntimeError("delegation runner needs gbot installed")

        planner = self._get_planner(model)
        # Suite-level cap respected per case if not overridden
        cap = case.get("max_tokens") or suite_config.get("default_max_tokens") or 500
        planner.max_tokens = cap

        start = time.monotonic()
        try:
            plan = await planner.plan(case["task"])
            error = None
        except Exception as e:
            logger.warning(
                f"delegation runner crashed on {case.get('id')}: {e}"
            )
            plan = {}
            error = str(e)
        latency_ms = int((time.monotonic() - start) * 1000)

        # Token + cost telemetry — read from the response the planner
        # exposed via ``last_response`` (Faz 22I model registry hookup).
        usage = {}
        last = getattr(planner, "last_response", None)
        if last is not None:
            usage = (last.response_metadata or {}).get("usage", {}) or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        cost_usd = pricing.calc_cost(model, prompt_tokens, completion_tokens)

        quality, detail = _score(case, plan)
        return CaseResult(
            case_id=case["id"],
            quality=quality,
            tokens_in=prompt_tokens,
            tokens_out=completion_tokens,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            error=error,
            detail={**detail, "plan": plan},
        )

    def _get_planner(self, model: str) -> DelegationPlanner:
        if model in self._planner_cache:
            return self._planner_cache[model]
        cfg = load_config()
        cfg.background.delegation.model = model  # override per call
        planner = DelegationPlanner(cfg, _TOOL_CATALOG)
        self._planner_cache[model] = planner
        return planner


def _score(case: dict, plan: dict) -> tuple[float, dict]:
    """Score a delegation plan against case expectations.

    Each component is 0/1; final score is mean of applicable parts.
    Components: execution, processor, tool keyword, multi-tool min,
    cron substring, delay range.
    """
    parts: list[float] = []
    detail: dict[str, Any] = {}

    exec_actual = (plan.get("execution") or "").lower()
    if "expected_execution" in case:
        exp = case["expected_execution"].lower()
        parts.append(1.0 if exec_actual == exp else 0.0)
        detail["execution"] = {"expected": exp, "actual": exec_actual}
    elif "expected_execution_in" in case:
        opts = [e.lower() for e in case["expected_execution_in"]]
        parts.append(1.0 if exec_actual in opts else 0.0)
        detail["execution"] = {"expected_in": opts, "actual": exec_actual}

    proc_actual = (plan.get("processor") or "").lower()
    opts = [p.lower() for p in case.get("expected_processor_in", [])]
    if opts:
        parts.append(1.0 if proc_actual in opts else 0.0)
        detail["processor"] = {"expected_in": opts, "actual": proc_actual}

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

    if "expected_tools_min" in case:
        n = len(plan.get("tools") or [])
        if not n and plan.get("tool_name"):
            n = 1
        ok = n >= case["expected_tools_min"]
        parts.append(1.0 if ok else 0.0)
        detail["tools_min"] = {
            "expected_min": case["expected_tools_min"],
            "got": n,
            "ok": ok,
        }

    if "expected_cron_substring" in case:
        cron = plan.get("cron_expr") or ""
        sub = case["expected_cron_substring"]
        ok = sub in cron
        parts.append(1.0 if ok else 0.0)
        detail["cron"] = {"expected_substring": sub, "actual": cron, "ok": ok}

    if "expected_delay_seconds_min" in case and "expected_delay_seconds_max" in case:
        delay = plan.get("delay_seconds")
        if isinstance(delay, (int, float)):
            ok = (
                case["expected_delay_seconds_min"]
                <= delay
                <= case["expected_delay_seconds_max"]
            )
        else:
            ok = False
        parts.append(1.0 if ok else 0.0)
        detail["delay"] = {
            "expected_range": [
                case["expected_delay_seconds_min"],
                case["expected_delay_seconds_max"],
            ],
            "actual": delay,
            "ok": ok,
        }

    quality = sum(parts) / len(parts) if parts else 0.0
    return quality, detail
