"""Configuration loader — YAML file + env override."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from gbot.core.config.schema import Config

# Eagerly populate process env from a top-level ``.env`` so plain keys
# (e.g. ``OPENROUTER_API_KEY``) reach the OpenRouter SDK and any other
# component that reads ``os.environ`` directly. ``python-dotenv`` was
# already a transitive dependency via LiteLLM; after Faz 22E Step 5K
# (LiteLLM removal) we wire it explicitly so this behaviour is preserved.
load_dotenv()


def load_config(config_path: str | Path | None = None) -> Config:
    """
    Load configuration.

    Resolution order for config file:
        1. Explicit ``config_path`` argument
        2. ``GRAPHBOT_CONFIG`` env variable
        3. ``./config.yaml`` in cwd

    Values priority:
        env vars (GBOT_*)  >  .env file  >  YAML (non-empty values)  >  defaults

    Empty strings and ``None`` values in YAML are treated as "not set" so that
    env vars can fill them in (otherwise pydantic init kwargs override env).
    """
    yaml_data = _load_yaml(_resolve_path(config_path))
    yaml_data = _strip_empty(yaml_data)

    # Build base Config from env vars + .env (BaseSettings standard flow)
    # Then merge YAML on top, but only for keys not set via env.
    env_cfg = Config()
    env_dump = env_cfg.model_dump()
    merged = _merge_yaml_under_env(yaml_data, env_dump, _env_set_keys())
    return Config(**merged)


def _env_set_keys(prefix: str = "GBOT_") -> set[str]:
    """Return dotted keys (e.g. 'auth.jwt_secret_key') that have env overrides."""
    keys: set[str] = set()
    for env_key in os.environ:
        if not env_key.startswith(prefix):
            continue
        path = env_key[len(prefix):].lower().split("__")
        keys.add(".".join(path))
    return keys


def _merge_yaml_under_env(
    yaml_data: dict[str, Any],
    env_dump: dict[str, Any],
    env_keys: set[str],
    prefix: str = "",
) -> dict[str, Any]:
    """Merge YAML on top of env defaults. Env-set fields take priority."""
    out: dict[str, Any] = dict(env_dump)
    for k, v in yaml_data.items():
        full = f"{prefix}{k}" if not prefix else f"{prefix}.{k}"
        if full in env_keys:
            continue  # env takes priority for this leaf
        if isinstance(v, dict) and isinstance(env_dump.get(k), dict):
            out[k] = _merge_yaml_under_env(v, env_dump[k], env_keys, full)
        else:
            out[k] = v
    return out


def _strip_empty(data: Any) -> Any:
    """Recursively remove keys whose value is an empty string or None.

    This lets env vars (GBOT_*) populate fields that the YAML left blank.
    Without this, ``jwt_secret_key: ""`` from YAML would override
    ``GBOT_AUTH__JWT_SECRET_KEY`` from the environment.
    """
    if isinstance(data, dict):
        cleaned: dict[str, Any] = {}
        for k, v in data.items():
            v = _strip_empty(v)
            if v == "" or v is None:
                continue
            if isinstance(v, dict) and not v:
                continue
            cleaned[k] = v
        return cleaned
    return data


def _resolve_path(config_path: str | Path | None = None) -> Path | None:
    """Resolve config file path."""
    if config_path:
        return Path(config_path)

    env = os.environ.get("GRAPHBOT_CONFIG")
    if env:
        return Path(env)

    # Try config/ directory first, then root fallback
    for candidate in (Path("config/config.yaml"), Path("config.yaml")):
        if candidate.exists():
            return candidate
    return None


def _load_yaml(path: Path | None) -> dict[str, Any]:
    """Load YAML file, return empty dict if not found."""
    if not path or not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}
