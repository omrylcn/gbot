"""Thin shim — pricing is owned by ``gbot.core.config.model_registry``
since v1.24.3. Kept here so existing callers (``gbot_eval/cli.py``,
``gbot_eval/capture.py``, ``gbot_eval/reporting.py``) keep working
without a coordinated rename.

Live OpenRouter price refreshes still write into
``gbot_eval/output/pricing_overrides.json`` — the registry layer reads
that file and shadows the in-tree ``config/models.yaml`` table.
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from gbot.core.config.model_registry import (
    add_pricing_override,
    all_models,
    calc_cost,
    get_profile,
)
from gbot.core.config.model_registry import reload as _reload_registry

__all__ = [
    "add_model",
    "calc_cost",
    "list_models",
    "refresh_from_openrouter",
    "PRICES",
]


_OVERRIDES_PATH = (
    Path(__file__).parent / "output" / "pricing_overrides.json"
)


class _PricesView(dict):
    """Read-only view backed by the model registry. Mutating raises.

    Some legacy code does ``PRICES[model] = {...}`` — route those to the
    overrides file via the registry rather than letting them mutate a
    detached dict that nobody else sees.
    """

    def __init__(self) -> None:
        super().__init__()
        # YAML-known models
        seen: set[str] = set()
        for m in all_models():
            p = get_profile(m)
            super().__setitem__(
                m, {"prompt": p.prompt_price, "completion": p.completion_price}
            )
            seen.add(m)
        # Override-only models (live OpenRouter dump etc.) so cost calc
        # still works for models that aren't curated in models.yaml.
        if _OVERRIDES_PATH.exists():
            try:
                raw = json.loads(_OVERRIDES_PATH.read_text())
            except (json.JSONDecodeError, OSError):
                raw = {}
            if isinstance(raw, dict):
                for m, v in raw.items():
                    if m in seen or not isinstance(v, dict):
                        continue
                    if "prompt" in v and "completion" in v:
                        super().__setitem__(
                            m,
                            {
                                "prompt": float(v["prompt"]),
                                "completion": float(v["completion"]),
                            },
                        )

    def __setitem__(self, key: str, value: dict) -> None:
        if not isinstance(value, dict) or "prompt" not in value or "completion" not in value:
            raise TypeError("PRICES entries must be {'prompt': float, 'completion': float}")
        add_pricing_override(key, float(value["prompt"]), float(value["completion"]))
        super().__setitem__(key, value)


PRICES = _PricesView()


def add_model(model: str, prompt: float, completion: float) -> None:
    """Persist a pricing override (CLI ``gbot-eval models add``)."""
    add_pricing_override(model, prompt, completion)
    PRICES[model] = {"prompt": prompt, "completion": completion}


def list_models() -> list[tuple[str, float, float]]:
    """For ``gbot-eval models``. Returns (model, prompt$, completion$)."""
    rows = []
    for m in all_models():
        p = get_profile(m)
        rows.append((m, p.prompt_price, p.completion_price))
    return rows


def refresh_from_openrouter(timeout: float = 15.0) -> dict[str, int | str]:
    """Pull live pricing from ``https://openrouter.ai/api/v1/models`` and
    write each entry into the overrides file. The next registry lookup
    will pick the new prices up automatically.
    """
    import httpx

    resp = httpx.get(
        "https://openrouter.ai/api/v1/models", timeout=timeout
    )
    resp.raise_for_status()
    payload = resp.json()
    items = payload.get("data") or payload.get("models") or []

    overrides: dict[str, dict[str, float]] = {}
    if _OVERRIDES_PATH.exists():
        try:
            existing = json.loads(_OVERRIDES_PATH.read_text())
            if isinstance(existing, dict):
                overrides = existing
        except (json.JSONDecodeError, OSError):
            overrides = {}

    added = 0
    for item in items:
        slug = item.get("id") or item.get("slug")
        pricing = item.get("pricing") or {}
        if not slug:
            continue
        try:
            prompt = float(pricing.get("prompt", 0)) * 1_000_000.0
            completion = float(pricing.get("completion", 0)) * 1_000_000.0
        except (TypeError, ValueError):
            continue
        if prompt == 0 and completion == 0:
            # Free models still useful to record; keep them
            pass
        full = f"openrouter/{slug}"
        overrides[full] = {"prompt": prompt, "completion": completion}
        added += 1

    _OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OVERRIDES_PATH.write_text(json.dumps(overrides, indent=2, sort_keys=True))
    _reload_registry()
    PRICES.clear()
    PRICES.update(
        {m: {"prompt": p.prompt_price, "completion": p.completion_price}
         for m, p in ((m, get_profile(m)) for m in all_models())}
    )
    logger.info(f"refresh_from_openrouter: {added} models updated")
    return {
        "updated": added,
        "source": "https://openrouter.ai/api/v1/models",
        "path": str(_OVERRIDES_PATH),
    }
