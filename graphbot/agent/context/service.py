"""ContextService — unified context inspection and management for all agent types."""

from __future__ import annotations

from typing import Any

from graphbot.agent.context.builder import ContextBuilder
from graphbot.agent.context.models import ContextOverride, LayerResult
from graphbot.agent.profiles import get_agent_md, get_agent_skills, get_profile
from graphbot.core.config.schema import Config
from graphbot.memory.store import MemoryStore


class ContextService:
    """Unified context inspection and management.

    Wraps ContextBuilder + profiles to provide:
    - Layer-by-layer inspection for all 3 agent types
    - Runtime overrides (in-memory, not persisted)
    - Full context preview
    """

    def __init__(self, config: Config, db: MemoryStore) -> None:
        self.config = config
        self.db = db
        self._builders: dict[str, ContextBuilder] = {}
        self._overrides: dict[str, dict[str, ContextOverride]] = {}

    def _get_builder(self, profile: str = "main") -> ContextBuilder:
        """Get or create a ContextBuilder for a profile."""
        if profile not in self._builders:
            self._builders[profile] = ContextBuilder(self.config, self.db, profile=profile)
        return self._builders[profile]

    # ── Main Agent Context ─────────────────────────────────

    def get_layers(
        self,
        user_id: str,
        profile: str = "main",
        role: str | None = None,
        context_layers: set[str] | None = None,
    ) -> dict[str, LayerResult]:
        """Get layer-by-layer breakdown for main agent context."""
        builder = self._get_builder(profile)
        layers = builder.build_layers(user_id, role, context_layers, mark_delivered=False)
        return self._apply_overrides(profile, layers)

    def preview(
        self,
        user_id: str,
        profile: str = "main",
        role: str | None = None,
        context_layers: set[str] | None = None,
    ) -> str:
        """Preview full rendered context string."""
        layers = self.get_layers(user_id, profile, role, context_layers)
        parts = [lr.content for lr in layers.values() if lr.enabled and lr.content]
        return "\n\n---\n\n".join(parts)

    # ── Planner Context ────────────────────────────────────

    def get_planner_context(self, tool_catalog: str = "") -> dict[str, Any]:
        """Inspect DelegationPlanner's context."""
        from graphbot.agent.delegation import _PLANNER_PROMPT

        template = get_agent_md("planner") or _PLANNER_PROMPT
        rendered = template.format(
            tool_catalog=tool_catalog or "[no catalog provided]",
            extra_examples="",
        )
        return {
            "profile": "planner",
            "template_source": "agents.yaml" if get_agent_md("planner") else "builtin",
            "template_vars": ["tool_catalog", "extra_examples"],
            "rendered_length": len(rendered),
            "rendered_tokens": len(rendered) // 4,
            "content": rendered,
        }

    # ── Light Agent Context ────────────────────────────────

    def get_light_context(self, task_prompt: str = "") -> dict[str, Any]:
        """Inspect LightAgent's context composition."""
        base = get_agent_md("light") or ""
        if base and task_prompt:
            full = f"{base}\n\n{task_prompt}"
        else:
            full = base or task_prompt
        return {
            "profile": "light",
            "base_md": base,
            "base_length": len(base),
            "task_prompt": task_prompt,
            "full_length": len(full),
            "full_tokens": len(full) // 4,
            "content": full,
        }

    # ── Profile Listing ────────────────────────────────────

    def list_profiles(self) -> list[dict[str, Any]]:
        """List all configured agent profiles."""
        profiles = []
        for name in ("main", "planner", "light"):
            p = get_profile(name)
            if p:
                profiles.append({
                    "name": name,
                    "agent_md": p.get("agent_md"),
                    "skills": p.get("skills", []),
                    "template_vars": p.get("template_vars", []),
                    "has_agent_md": get_agent_md(name) is not None,
                })
        return profiles

    def get_profile_detail(self, name: str) -> dict[str, Any]:
        """Get profile detail including AGENT.md content."""
        p = get_profile(name)
        md_content = get_agent_md(name)
        return {
            "name": name,
            "config": p,
            "agent_md_content": md_content,
            "agent_md_length": len(md_content) if md_content else 0,
            "skills": get_agent_skills(name),
        }

    # ── Runtime Overrides ──────────────────────────────────

    def set_override(
        self,
        profile: str,
        layer: str,
        content: str | None = None,
        enabled: bool = True,
    ) -> None:
        """Set a runtime override for a layer. Not persisted."""
        if profile not in self._overrides:
            self._overrides[profile] = {}
        self._overrides[profile][layer] = ContextOverride(content=content, enabled=enabled)

    def clear_override(self, profile: str, layer: str) -> None:
        """Remove a runtime override."""
        if profile in self._overrides:
            self._overrides[profile].pop(layer, None)

    def clear_all_overrides(self, profile: str | None = None) -> None:
        """Clear all overrides for a profile, or all profiles."""
        if profile:
            self._overrides.pop(profile, None)
        else:
            self._overrides.clear()

    def get_overrides(self, profile: str) -> dict[str, ContextOverride]:
        """Get current overrides for a profile."""
        return dict(self._overrides.get(profile, {}))

    def _apply_overrides(
        self, profile: str, layers: dict[str, LayerResult]
    ) -> dict[str, LayerResult]:
        """Apply runtime overrides to layer results."""
        overrides = self._overrides.get(profile, {})
        if not overrides:
            return layers
        for name, lr in layers.items():
            if name in overrides:
                ov = overrides[name]
                if ov.content is not None:
                    layers[name] = lr.model_copy(update={
                        "content": ov.content,
                        "chars": len(ov.content),
                        "tokens": len(ov.content) // 4,
                    })
                if not ov.enabled:
                    layers[name] = layers[name].model_copy(update={"enabled": False})
        return layers
