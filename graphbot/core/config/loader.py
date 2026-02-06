"""Configuration loader — YAML file + env override."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from graphbot.core.config.schema import Config


def load_config(config_path: str | Path | None = None) -> Config:
    """
    Load configuration.

    Resolution order for config file:
        1. Explicit ``config_path`` argument
        2. ``GRAPHBOT_CONFIG`` env variable
        3. ``./config.yaml`` in cwd

    Values priority (handled by pydantic-settings):
        env vars  >  .env file  >  YAML  >  defaults
    """
    yaml_data = _load_yaml(_resolve_path(config_path))
    return Config(**yaml_data)


def _resolve_path(config_path: str | Path | None = None) -> Path | None:
    """Resolve config file path."""
    if config_path:
        return Path(config_path)

    env = os.environ.get("GRAPHBOT_CONFIG")
    if env:
        return Path(env)

    default = Path("config.yaml")
    return default if default.exists() else None


def _load_yaml(path: Path | None) -> dict[str, Any]:
    """Load YAML file, return empty dict if not found."""
    if not path or not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}
