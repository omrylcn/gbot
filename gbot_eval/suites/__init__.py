"""Suite registry — combines legacy Python suites and YAML-backed suites.

During the v1.22.0 migration both shapes coexist. As each suite gets
its YAML equivalent, the legacy Python file is deleted and only the
YAML import remains. After all 8 suites migrate, the legacy import
block can be removed entirely.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from loguru import logger

from gbot_eval.suites.base import Suite, YamlBackedSuite

SUITE_REGISTRY: dict[str, Suite] = {}


def register(suite: Suite) -> Suite:
    """Decorator-friendly registration: ``SUITE_REGISTRY[name] = suite``."""
    if suite.name in SUITE_REGISTRY:
        raise ValueError(f"duplicate suite name: {suite.name}")
    SUITE_REGISTRY[suite.name] = suite
    return suite


def _gbot_installed() -> bool:
    try:
        import gbot  # noqa: F401

        return True
    except ImportError:
        return False


def _load_yaml_suites() -> None:
    """Glob ``suites/*.yaml`` and register every one.

    YAML wins on naming conflicts with legacy Python suites — so the
    migration can land one suite at a time without breaking imports.
    """
    suites_dir = Path(__file__).parent
    catalogs_dir = suites_dir.parent / "catalogs"
    gbot_avail = _gbot_installed()

    for path in sorted(suites_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text())
        except yaml.YAMLError as e:
            logger.warning(f"yaml suite '{path.name}' parse failed: {e}")
            continue
        if not isinstance(data, dict) or "name" not in data:
            logger.warning(
                f"yaml suite '{path.name}' missing 'name' field; skipped"
            )
            continue
        if data.get("requires_gbot") and not gbot_avail:
            logger.info(
                f"skipping suite '{data['name']}' — requires gbot, not installed"
            )
            continue
        # Resolve catalog references (one level deep).
        runner_cfg = data.get("runner_config") or {}
        if isinstance(runner_cfg.get("tools_catalog"), str):
            cat_path = (catalogs_dir / runner_cfg["tools_catalog"]).resolve()
            if cat_path.exists():
                runner_cfg["tools_catalog"] = yaml.safe_load(
                    cat_path.read_text()
                )
                data["runner_config"] = runner_cfg
        suite = YamlBackedSuite(config=data, yaml_path=path)
        SUITE_REGISTRY[suite.name] = suite  # YAML overrides legacy Python


def _load_legacy_python_suites() -> None:
    """Import the surviving Python-class suites — best-effort.

    Each module is wrapped in try/except so a half-deleted state during
    migration doesn't crash the registry. Once a Python file is gone,
    its import here just no-ops.
    """
    legacy_modules = [
        "agent_delegation",
        "agent_instruction",
        "agent_structured",
        "agent_tool_calling",
        "memory_audn",
        "memory_extraction",
        "memory_page_compile",
        "stress_long_context",
    ]
    for mod in legacy_modules:
        try:
            __import__(f"gbot_eval.suites.{mod}")
        except (ImportError, ModuleNotFoundError):
            pass  # File deleted as part of the YAML migration — fine.


def _load_suites() -> None:
    """Order matters — load Python legacy first, then YAML overrides them."""
    _load_legacy_python_suites()
    _load_yaml_suites()


def list_names() -> list[str]:
    return sorted(SUITE_REGISTRY.keys())


def filter_suites(spec: str | None) -> list[Suite]:
    """Resolve a CLI filter spec to suites.

    ``spec`` examples:
    - ``None`` or ``""`` → tüm suite'ler
    - ``"memory"`` → tüm memory.* suite'ler
    - ``"memory.extraction"`` → tek suite
    - ``"memory,agent.delegation"`` → virgüllü list
    """
    if not spec:
        return [SUITE_REGISTRY[n] for n in list_names()]

    parts = [p.strip() for p in spec.split(",") if p.strip()]
    selected: list[Suite] = []
    for part in parts:
        if part in SUITE_REGISTRY:
            selected.append(SUITE_REGISTRY[part])
            continue
        # group filter — match anything starting with f"{part}."
        prefix = f"{part}."
        matches = [
            SUITE_REGISTRY[n] for n in list_names() if n.startswith(prefix)
        ]
        if not matches:
            raise KeyError(f"no suite matches '{part}'")
        selected.extend(matches)
    # de-dup preserving order
    seen: set[str] = set()
    out: list[Suite] = []
    for s in selected:
        if s.name not in seen:
            seen.add(s.name)
            out.append(s)
    return out


_load_suites()
