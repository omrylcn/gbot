"""Runner registry — selects how each YAML suite is executed.

Suite YAMLs declare ``runner: <name>``; this registry maps the name
to a ``Runner`` instance. Built-in runners register themselves at
import time via ``@register("name")``.

gbot-bound runners (``memory_extraction``, etc.) wrap their gbot
imports in try/except so this package can still be imported when
gbot isn't installed.
"""

from __future__ import annotations

from gbot_eval.runners.base import Runner

RUNNER_REGISTRY: dict[str, Runner] = {}


def register(name: str):
    """Decorator: ``@register("chat_completion")``."""

    def decorator(cls):
        instance = cls() if isinstance(cls, type) else cls
        if instance.name != name:
            raise ValueError(
                f"runner name mismatch: decorator says '{name}', "
                f"class says '{instance.name}'"
            )
        if name in RUNNER_REGISTRY:
            raise ValueError(f"duplicate runner name: {name}")
        RUNNER_REGISTRY[name] = instance
        return cls

    return decorator


def list_names() -> list[str]:
    return sorted(RUNNER_REGISTRY.keys())


def _load_runners() -> None:
    """Import-time side-effect registration for shipped runners."""
    from gbot_eval.runners import chat_completion  # noqa: F401
    # gbot-bound runners — Step 6D
    try:
        from gbot_eval.runners import delegation  # noqa: F401
        from gbot_eval.runners import memory_audn  # noqa: F401
        from gbot_eval.runners import memory_extraction  # noqa: F401
        from gbot_eval.runners import memory_page_compile  # noqa: F401
    except ImportError:
        # Modules don't exist yet (Step 6D adds them) — skip silently.
        pass
    # Multi-turn — Step 6E
    try:
        from gbot_eval.runners import multi_turn  # noqa: F401
    except ImportError:
        pass


_load_runners()


__all__ = [
    "RUNNER_REGISTRY",
    "Runner",
    "list_names",
    "register",
]
