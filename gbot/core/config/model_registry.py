"""Model registry — loads ``config/models.yaml`` at import time and
exposes per-model defaults to both the production LLM provider and the
gbot-eval CLI.

Design rationale
----------------
Before this module: each call site juggled its own ``max_tokens``,
``thinking`` and OpenRouter ``reasoning`` flag, with hard-coded values
in ``OpenRouterLLM.achat`` and a separate static pricing table in
``gbot_eval/pricing.py``. New models needed code edits in two places.

After: ``config/models.yaml`` is the single source of truth. Adding a
new model is a YAML edit; the same registry is consulted by the
production agent, the delegation planner, every memory pipeline call,
and the eval runner.

Lookup priority at every LLM call:

1. Explicit caller argument (``max_tokens=...`` etc.)
2. ``models[<model_id>]`` from YAML
3. ``defaults`` block from YAML
4. Hard-coded fallback inside ``ModelProfile.fallback()``

Optional user-side overrides (from CLI ``gbot-eval models add``) live in
``gbot_eval/output/pricing_overrides.json`` and shadow the YAML so ad-hoc
additions don't pollute the committed file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

_REGISTRY_PATH = Path(__file__).resolve().parents[3] / "config" / "models.yaml"
_OVERRIDES_PATH = (
    Path(__file__).resolve().parents[3]
    / "gbot_eval"
    / "output"
    / "pricing_overrides.json"
)


@dataclass(frozen=True)
class ModelProfile:
    """Per-model defaults resolved from YAML + overrides."""

    model: str
    thinking: bool = False
    reasoning_effort: str | None = None  # None | "none" | "low" | "medium" | "high"
    max_tokens: int = 4096
    temperature: float = 0.7
    prompt_price: float = 0.0  # $/1M tokens
    completion_price: float = 0.0
    notes: str = ""
    is_known: bool = True

    @classmethod
    def fallback(cls, model: str) -> "ModelProfile":
        """Sensible defaults when a model isn't in the registry. Logs once."""
        _warn_unknown(model)
        return cls(model=model, is_known=False)


@dataclass
class _Registry:
    defaults: dict[str, Any] = field(default_factory=dict)
    models: dict[str, dict[str, Any]] = field(default_factory=dict)
    overrides: dict[str, dict[str, float]] = field(default_factory=dict)

    def lookup(self, model: str) -> ModelProfile:
        if model not in self.models:
            return ModelProfile.fallback(model)
        entry = {**self.defaults, **self.models[model]}
        pricing = {**self.defaults.get("pricing", {}), **entry.get("pricing", {})}
        # User CLI override wins (committed YAML stays clean).
        if model in self.overrides:
            pricing = {**pricing, **self.overrides[model]}
        return ModelProfile(
            model=model,
            thinking=bool(entry.get("thinking", False)),
            reasoning_effort=entry.get("reasoning_effort"),
            max_tokens=int(entry.get("max_tokens", 4096)),
            temperature=float(entry.get("temperature", 0.7)),
            prompt_price=float(pricing.get("prompt", 0.0)),
            completion_price=float(pricing.get("completion", 0.0)),
            notes=str(entry.get("notes", "")),
        )

    def all_models(self) -> list[str]:
        return sorted(self.models.keys())

    def add(self, model: str, prompt: float, completion: float) -> None:
        """Persist a pricing override so it survives across runs."""
        self.overrides[model] = {"prompt": prompt, "completion": completion}
        _OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        _OVERRIDES_PATH.write_text(json.dumps(self.overrides, indent=2))


def _load_registry() -> _Registry:
    reg = _Registry()
    if _REGISTRY_PATH.exists():
        try:
            data = yaml.safe_load(_REGISTRY_PATH.read_text(encoding="utf-8")) or {}
            reg.defaults = data.get("defaults", {}) or {}
            reg.models = data.get("models", {}) or {}
        except (yaml.YAMLError, OSError) as e:
            logger.warning(f"models.yaml load failed: {e} — using fallback defaults")
    else:
        logger.warning(
            f"models.yaml not found at {_REGISTRY_PATH} — every model will use fallback defaults"
        )

    if _OVERRIDES_PATH.exists():
        try:
            raw = json.loads(_OVERRIDES_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                reg.overrides = {
                    m: {"prompt": float(v["prompt"]), "completion": float(v["completion"])}
                    for m, v in raw.items()
                    if isinstance(v, dict) and "prompt" in v and "completion" in v
                }
        except (json.JSONDecodeError, OSError, ValueError, KeyError) as e:
            logger.warning(f"pricing overrides invalid: {e}")

    return reg


_REGISTRY: _Registry = _load_registry()
_warned: set[str] = set()


def _warn_unknown(model: str) -> None:
    if model in _warned:
        return
    _warned.add(model)
    logger.warning(
        f"model '{model}' not in config/models.yaml — using fallback defaults "
        f"(thinking=False, max_tokens=4096, cost=$0). "
        f"Add via 'gbot-eval models add' or edit config/models.yaml."
    )


# ── Public API ────────────────────────────────────────────────────


def get_profile(model: str) -> ModelProfile:
    """Return resolved defaults for ``model``. Falls back gracefully."""
    return _REGISTRY.lookup(model)


def all_models() -> list[str]:
    """List of every model_id known to the registry."""
    return _REGISTRY.all_models()


def add_pricing_override(model: str, prompt: float, completion: float) -> None:
    """Persist a CLI-side pricing addition (gitignored overrides file)."""
    _REGISTRY.add(model, prompt, completion)


def calc_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """USD cost for a single LLM call.

    Looks at YAML registry first, falls back to ``pricing_overrides.json``
    for models that are recorded only via ``gbot-eval models refresh``
    (the live OpenRouter dump). Returns 0 only when the model is unknown
    everywhere.
    """
    profile = get_profile(model)
    if profile.is_known or profile.prompt_price or profile.completion_price:
        return (
            prompt_tokens * profile.prompt_price
            + completion_tokens * profile.completion_price
        ) / 1_000_000.0
    override = _REGISTRY.overrides.get(model)
    if override:
        return (
            prompt_tokens * override["prompt"]
            + completion_tokens * override["completion"]
        ) / 1_000_000.0
    return 0.0


def reload() -> None:
    """Re-read YAML + overrides. Useful in tests; production reads once."""
    global _REGISTRY
    _REGISTRY = _load_registry()
    _warned.clear()
