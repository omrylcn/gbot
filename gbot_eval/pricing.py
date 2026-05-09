"""Model price table for cost estimates.

Prices are expressed as USD per **1 million** tokens. Source: OpenRouter
public pricing pages, sampled at ``_LAST_UPDATED``. Update via
``gbot-eval models add`` or by editing this file.

User-added entries land in ``gbot_eval/output/pricing_overrides.json``
(gitignored) and merge in at import time, so the in-tree table stays
clean while ad-hoc additions are still picked up automatically.

Unknown models cost 0 with a warning; the run still completes.
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

_LAST_UPDATED = "2026-05-09"


PRICES: dict[str, dict[str, float]] = {
    # Google
    "openrouter/google/gemini-3-flash-preview":   {"prompt": 0.075, "completion": 0.30},
    "openrouter/google/gemini-3.1-flash-lite":    {"prompt": 0.05,  "completion": 0.20},
    "openrouter/google/gemini-2.5-flash":         {"prompt": 0.075, "completion": 0.30},
    "openrouter/google/gemini-2.5-pro":           {"prompt": 1.25,  "completion": 10.00},
    # Anthropic
    "openrouter/anthropic/claude-haiku-4.5":    {"prompt": 1.00,  "completion": 5.00},
    "openrouter/anthropic/claude-sonnet-4.5":   {"prompt": 3.00,  "completion": 15.00},
    "openrouter/anthropic/claude-opus-4":       {"prompt": 15.00, "completion": 75.00},
    # OpenAI
    "openrouter/openai/gpt-4o-mini":            {"prompt": 0.15,  "completion": 0.60},
    "openrouter/openai/gpt-4o":                 {"prompt": 2.50,  "completion": 10.00},
    "openrouter/openai/gpt-5-mini":             {"prompt": 0.25,  "completion": 2.00},
    # Moonshot
    "openrouter/moonshotai/kimi-k2.5":          {"prompt": 0.14,  "completion": 2.49},
    "openrouter/moonshotai/kimi-k2.6":          {"prompt": 0.12,  "completion": 0.24},
    # DeepSeek
    "openrouter/deepseek/deepseek-v3.2":        {"prompt": 0.27,  "completion": 1.10},
    "openrouter/deepseek/deepseek-r1":          {"prompt": 0.55,  "completion": 2.19},
    # MiniMax
    "openrouter/minimax/minimax-m2.5":           {"prompt": 0.30,  "completion": 1.20},
    "openrouter/minimax/minimax-m2.7":           {"prompt": 0.30,  "completion": 1.20},
    # Qwen
    "openrouter/qwen/qwen-3-235b-instruct":      {"prompt": 0.20,  "completion": 0.60},
    # GLM (z-ai / zhipuai)
    "openrouter/zhipuai/glm-4.7-flash":          {"prompt": 0.10,  "completion": 0.30},
    "openrouter/zhipuai/glm-5":                  {"prompt": 0.50,  "completion": 1.50},
    "openrouter/z-ai/glm-5.1":                   {"prompt": 0.50,  "completion": 1.50},
}


def calc_cost(
    model: str, prompt_tokens: int, completion_tokens: int
) -> float:
    """USD cost for a single LLM call.

    Returns 0.0 for unknown models (with a one-time warning per model).
    """
    pricing = PRICES.get(model)
    if pricing is None:
        _warn_unknown(model)
        return 0.0
    return (
        prompt_tokens * pricing["prompt"]
        + completion_tokens * pricing["completion"]
    ) / 1_000_000.0


_warned: set[str] = set()


def _warn_unknown(model: str) -> None:
    if model in _warned:
        return
    _warned.add(model)
    logger.warning(
        f"gbot-eval: no pricing for '{model}' — "
        f"cost will report as $0. Add via 'gbot-eval models add'."
    )


def list_models() -> list[tuple[str, float, float]]:
    """For the ``gbot-eval models`` command. Returns (model, prompt$, completion$)."""
    return [
        (m, p["prompt"], p["completion"])
        for m, p in sorted(PRICES.items())
    ]


# ── User-side pricing overrides ─────────────────────────────────

_OVERRIDES_PATH = (
    Path(__file__).parent / "output" / "pricing_overrides.json"
)


def _load_overrides() -> None:
    """Merge user-added pricing entries from
    ``gbot_eval/output/pricing_overrides.json`` into ``PRICES``.

    Silent no-op if the file doesn't exist or is malformed; logs a
    warning rather than failing the import.
    """
    if not _OVERRIDES_PATH.exists():
        return
    try:
        data = json.loads(_OVERRIDES_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"pricing overrides invalid: {e}")
        return
    if not isinstance(data, dict):
        return
    for model, entry in data.items():
        if (
            isinstance(entry, dict)
            and "prompt" in entry
            and "completion" in entry
        ):
            PRICES[model] = {
                "prompt": float(entry["prompt"]),
                "completion": float(entry["completion"]),
            }


def add_model(model: str, prompt: float, completion: float) -> None:
    """Persist a new pricing entry to the overrides file."""
    overrides: dict = {}
    if _OVERRIDES_PATH.exists():
        try:
            overrides = json.loads(_OVERRIDES_PATH.read_text())
            if not isinstance(overrides, dict):
                overrides = {}
        except (json.JSONDecodeError, OSError):
            overrides = {}
    overrides[model] = {
        "prompt": float(prompt),
        "completion": float(completion),
    }
    _OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OVERRIDES_PATH.write_text(json.dumps(overrides, indent=2))
    PRICES[model] = overrides[model]


_load_overrides()
