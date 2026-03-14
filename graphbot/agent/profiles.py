"""Agent profiles — load agents.yaml and resolve AGENT.md / skills per agent type."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from loguru import logger

_PROFILES_DATA: dict[str, Any] | None = None


def _load_agents_yaml(path: str | Path | None = None) -> dict[str, Any]:
    """Load and cache agents.yaml. Returns empty dict if file missing."""
    global _PROFILES_DATA
    if _PROFILES_DATA is not None:
        return _PROFILES_DATA

    if path is None:
        # Try config/ directory first, then root fallback
        path = Path("config/agents.yaml")
        if not path.exists():
            path = Path("agents.yaml")
    else:
        path = Path(path)

    if not path.exists():
        logger.warning(f"agents.yaml not found at {path}, using defaults")
        _PROFILES_DATA = {}
        return _PROFILES_DATA

    with open(path, encoding="utf-8") as f:
        _PROFILES_DATA = yaml.safe_load(f) or {}
    agents = list((_PROFILES_DATA.get("agents") or {}).keys())
    logger.info(f"Loaded agents.yaml: {agents} profiles")
    return _PROFILES_DATA


def reset_cache() -> None:
    """Clear cached profiles data (for testing)."""
    global _PROFILES_DATA
    _PROFILES_DATA = None


def get_profile(name: str, path: str | Path | None = None) -> dict[str, Any]:
    """Get agent profile by name.

    Parameters
    ----------
    name : str
        Profile name (e.g. 'main', 'planner', 'light').
    path : str or Path, optional
        Path to agents.yaml.

    Returns
    -------
    dict
        Profile config dict, or empty dict if not found.
    """
    data = _load_agents_yaml(path)
    agents = data.get("agents", {})
    return agents.get(name, {})


def get_agent_md(name: str, path: str | Path | None = None) -> str | None:
    """Read the AGENT.md content for a profile.

    Returns
    -------
    str or None
        AGENT.md content, or None if file not found / not configured.
    """
    profile = get_profile(name, path)
    md_path_str = profile.get("agent_md")
    if not md_path_str:
        return None

    md_path = Path(md_path_str)
    if not md_path.exists():
        logger.debug(f"AGENT.md not found for profile '{name}': {md_path}")
        return None

    return md_path.read_text(encoding="utf-8")


def get_agent_skills(name: str, path: str | Path | None = None) -> list[str]:
    """Get skill names for a profile.

    Returns
    -------
    list[str]
        Skill names. ["*"] means all skills. Empty list means no skills.
    """
    profile = get_profile(name, path)
    return profile.get("skills", [])


def get_template_vars(name: str, path: str | Path | None = None) -> list[str]:
    """Get template variable names for a profile.

    Returns
    -------
    list[str]
        Variable names expected in the AGENT.md template.
    """
    profile = get_profile(name, path)
    return profile.get("template_vars", [])
