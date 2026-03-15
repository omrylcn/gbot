"""ContextService — unified context inspection and management for all agent types."""

from __future__ import annotations

from typing import Any

from gbot.agent.context.builder import ContextBuilder
from gbot.agent.context.models import ContextOverride, LayerResult
from gbot.agent.profiles import (
    get_agent_md,
    get_agent_skills,
    get_profile,
    get_profile_context_layers,
    get_template_vars,
)
from gbot.core.config.schema import Config
from gbot.memory.store import MemoryStore


class ContextService:
    """Unified context inspection and management.

    Wraps ContextBuilder + profiles to provide:
    - Layer-by-layer inspection for all 3 agent types
    - Runtime overrides (in-memory, not persisted)
    - Full context preview
    """

    def __init__(self, config: Config, db: MemoryStore, registry: Any = None) -> None:
        self.config = config
        self.db = db
        self._registry = registry
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
        """Get layer-by-layer breakdown for main agent context.

        Includes informational layers for message history and tool definitions
        to show the complete LLM input picture.
        """
        builder = self._get_builder(profile)
        layers = builder.build_layers(user_id, role, context_layers, mark_delivered=False)
        layers = self._apply_overrides(profile, layers)

        # Informational: message history stats
        active = self.db.get_active_session(user_id)
        if active:
            msgs = self.db.get_session_messages(active["session_id"])
            msg_count = len(msgs)
            msg_text = "\n".join(f"[{m['role']}] {(m.get('content') or '')[:80]}" for m in msgs[-10:])
            if msg_count > 10:
                msg_text = f"... ({msg_count - 10} earlier messages)\n{msg_text}"
            layers["message_history"] = LayerResult(
                name="message_history",
                description=f"Session message history ({msg_count} messages)",
                source=f"messages table — session {active['session_id'][:8]}...",
                content=msg_text,
                chars=sum(len(m.get("content") or "") for m in msgs),
                tokens=sum(len(m.get("content") or "") for m in msgs) // 4,
                budget=0, truncated=False, enabled=True,
            )
        else:
            layers["message_history"] = LayerResult(
                name="message_history",
                description="Session message history (no active session)",
                source="messages table (SQLite)",
                content="", chars=0, tokens=0,
                budget=0, truncated=False, enabled=True,
            )

        # Informational: tool definitions
        if self._registry:
            import json
            from langchain_core.utils.function_calling import convert_to_openai_function
            tools = self._registry.get_all_tools()
            tool_defs = [
                {"type": "function", "function": convert_to_openai_function(t)}
                for t in tools
            ]
            tool_json = json.dumps(tool_defs, ensure_ascii=False)
            tool_names = [t.name for t in tools]
            layers["tool_definitions"] = LayerResult(
                name="tool_definitions",
                description=f"OpenAI function schemas ({len(tools)} tools)",
                source="ToolRegistry — sent as 'tools' parameter",
                content=", ".join(tool_names),
                chars=len(tool_json),
                tokens=len(tool_json) // 4,
                budget=0, truncated=False, enabled=True,
            )

        return layers

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

    def get_planner_context(
        self, user_id: str | None = None, tool_catalog: str = ""
    ) -> dict[str, LayerResult]:
        """Build planner context via layer system.

        Uses context_layers from agents.yaml (default: identity only).
        Template vars (tool_catalog, extra_examples) are injected into identity.
        """
        uid = user_id or self.config.owner_user_id or "default"
        profile_layers = get_profile_context_layers("planner")
        tvars = {"tool_catalog": tool_catalog or "[no catalog provided]", "extra_examples": ""}
        builder = self._get_builder("planner")
        layers = builder.build_layers(uid, context_layers=profile_layers, template_vars=tvars)
        return self._apply_overrides("planner", layers)

    # ── Light Agent Context ────────────────────────────────

    def get_light_context(
        self, user_id: str | None = None, task_prompt: str = ""
    ) -> dict[str, LayerResult]:
        """Inspect LightAgent's context composition.

        LightAgent is a special-purpose agent with its own context model:
        AGENT.md (base identity) + task_prompt (injected at runtime).
        Not built via layer system — shown as layers for Dashboard consistency.
        """
        base = get_agent_md("light") or ""
        layers: dict[str, LayerResult] = {}
        layers["identity"] = LayerResult(
            name="identity",
            description="Light agent base identity",
            source="workspace/agents/light/AGENT.md",
            content=base,
            chars=len(base),
            tokens=len(base) // 4,
            budget=0, truncated=False, enabled=True,
        )
        if task_prompt:
            layers["task_prompt"] = LayerResult(
                name="task_prompt",
                description="Background task instructions",
                source="DelegationPlanner or cron job",
                content=task_prompt,
                chars=len(task_prompt),
                tokens=len(task_prompt) // 4,
                budget=0, truncated=False, enabled=True,
            )
        return layers

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
