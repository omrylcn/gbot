"""Suite registry. Each suite module imports itself via ``register()``
during import; the listing ``_load_suites()`` at the bottom triggers
that side effect for every shipped suite.
"""

from __future__ import annotations

from gbot_eval.suites.base import Suite

SUITE_REGISTRY: dict[str, Suite] = {}


def register(suite: Suite) -> Suite:
    """Decorator-friendly registration: ``SUITE_REGISTRY[name] = suite``."""
    if suite.name in SUITE_REGISTRY:
        raise ValueError(f"duplicate suite name: {suite.name}")
    SUITE_REGISTRY[suite.name] = suite
    return suite


def _load_suites() -> None:
    """Import every shipped suite so its ``register()`` side effect runs.

    Called once from this module's bottom. Adding a new suite:
    1. Create ``gbot_eval/suites/<name>.py`` that calls ``register(...)``
    2. Append the import here.
    """
    from gbot_eval.suites import memory_audn  # noqa: F401
    from gbot_eval.suites import memory_extraction  # noqa: F401
    from gbot_eval.suites import memory_page_compile  # noqa: F401


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
