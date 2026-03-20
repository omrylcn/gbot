"""ContextBuilder — assembles system prompt from SQLite + workspace files."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from gbot.agent.context.models import LayerResult
from gbot.agent.profiles import get_agent_md, get_agent_skills
from gbot.agent.skills.loader import SkillLoader
from gbot.core.config.schema import Config
from gbot.memory.store import MemoryStore


class ContextBuilder:
    """Builds layered system prompt with configurable persona, roles, and token budgets.

    Layers:
      1. Identity (prompt_template > system_prompt > AGENT.md > persona config)
      2. Runtime info (user_id, datetime)
      3. Current role (if configured)
      4. Agent memory (agent_memory table)
      5. User context (notes, favorites, preferences)
      6. Previous session summary
      7. Skills (always-on + index)
    """

    def __init__(self, config: Config, db: MemoryStore, profile: str = "main"):
        self.config = config
        self.db = db
        self.profile = profile
        self.skills = SkillLoader(
            workspace=config.workspace_path,
            builtin_dir=Path(__file__).parent.parent / "skills" / "builtin",
        )

    def build_layers(
        self,
        user_id: str,
        role: str | None = None,
        context_layers: set[str] | None = None,
        mark_delivered: bool = False,
        template_vars: dict[str, str] | None = None,
    ) -> dict[str, LayerResult]:
        """Build each context layer individually.

        Parameters
        ----------
        user_id : str
            Target user ID.
        role : str, optional
            Override role name.
        context_layers : set[str], optional
            Allowed layers from RBAC. None = all.
        mark_delivered : bool
            If True, mark events as delivered (side-effect).
            Use False for preview/inspection.

        Returns
        -------
        dict[str, LayerResult]
            Ordered dict of layer_name -> LayerResult.
        """
        priorities = self.config.assistant.context_priorities
        layers: dict[str, LayerResult] = {}

        def _allowed(layer: str) -> bool:
            return context_layers is None or layer in context_layers

        # Layer metadata: description and source
        _LAYER_META: dict[str, tuple[str, str]] = {
            "identity": ("Bot identity, persona and rules", "workspace/AGENT.md or persona config"),
            "runtime": ("Current user, datetime, model info", "Config + datetime.now()"),
            "role": ("RBAC role description (guest only)", "config/roles.yaml"),
            "agent_memory": ("Long-term facts the bot remembers", "agent_memory table (SQLite)"),
            "user_context": ("Notes, preferences, favorites", "user_notes + preferences + favorites (SQLite)"),
            "session_summary": ("Previous session LLM summary", "sessions table (SQLite)"),
            "skills": ("Always-on skill content", "SKILL.md files (workspace + builtin)"),
            "skills_index": ("Available skills for load_skill()", "SKILL.md files (workspace + builtin)"),
        }

        def _add(name: str, content: str, budget: int = 0) -> None:
            truncated_content = self._truncate(content, budget) if budget else content
            was_truncated = len(truncated_content) < len(content)
            desc, src = _LAYER_META.get(name, ("", ""))
            layers[name] = LayerResult(
                name=name,
                description=desc,
                source=src,
                content=truncated_content,
                chars=len(truncated_content),
                tokens=len(truncated_content) // 4,
                budget=budget,
                truncated=was_truncated,
                enabled=True,
            )

        def _add_empty(name: str) -> None:
            desc, src = _LAYER_META.get(name, ("", ""))
            layers[name] = LayerResult(
                name=name, description=desc, source=src,
                content="", chars=0, tokens=0,
                budget=0, truncated=False, enabled=True,
            )

        # 1. Identity
        if _allowed("identity"):
            identity = self._get_identity()
            if identity and template_vars:
                try:
                    identity = identity.format(**template_vars)
                except (KeyError, ValueError):
                    pass  # template vars not found, use as-is
            if identity:
                _add("identity", identity, priorities.identity)

        # 2. Runtime info
        if _allowed("runtime"):
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            user = self.db.get_user(user_id)
            user_name = user["name"] if user and user.get("name") else user_id
            _add("runtime", (
                f"# Runtime\n\n"
                f"- Current user_id: {user_id}\n"
                f"- Current user_name: {user_name}\n"
                f"- Current time: {now}\n"
                f"- Use this user_id when calling tools that require it."
            ))

        # 3. Current role
        if _allowed("role"):
            role_text = self._get_role(role)
            if role_text:
                _add("role", f"# Current Role\n\n{role_text}")
            else:
                _add_empty("role")

        # 4. Agent memory
        if _allowed("agent_memory"):
            memory = self.db.read_memory("long_term")
            if memory:
                _add("agent_memory", f"# Agent Memory\n\n{memory}", priorities.agent_memory)
            else:
                _add_empty("agent_memory")

        # 5. User context (explicit notes + learned facts)
        if _allowed("user_context"):
            # Explicit: notes, preferences, favorites
            user_ctx = self.db.get_user_context(user_id)

            # Learned: memory_facts (auto-extracted from conversations)
            learned = ""
            try:
                facts = self.db.get_facts(user_id, valid_only=True, limit=15)
                if facts:
                    lines = "\n".join(f"- {f['content']}" for f in facts)
                    learned = f"LEARNED FACTS:\n{lines}"
            except Exception:
                pass  # memory_facts table may not exist in tests

            combined = "\n\n".join(p for p in [user_ctx, learned] if p)
            if combined:
                _add("user_context", f"# User Context\n\n{combined}", priorities.user_context)
            else:
                _add_empty("user_context")

        # 6. Previous session summary
        if _allowed("session_summary"):
            summary = self.db.get_last_session_summary(user_id)
            if summary:
                _add(
                    "session_summary",
                    f"# Previous Conversation\n\n{summary}",
                    priorities.session_summary,
                )
            else:
                _add_empty("session_summary")

        # 8. Skills
        if _allowed("skills"):
            profile_skills = get_agent_skills(self.profile)
            if profile_skills != []:
                always_on = self.skills.get_always_on()
                if profile_skills != ["*"] and profile_skills:
                    allowed_names = set(profile_skills)
                    always_on = [s for s in always_on if s.name in allowed_names]
                if always_on:
                    skill_texts = [self.skills.load_content(s.name) for s in always_on]
                    active = "\n\n---\n\n".join(t for t in skill_texts if t)
                    if active:
                        _add("skills", f"# Active Skills\n\n{active}", priorities.skills)

                index = self.skills.build_index()
                if index:
                    _add("skills_index", (
                        "# Available Skills\n\n"
                        "Use load_skill(skill_name) to load detailed instructions when needed.\n\n"
                        + index
                    ))

        return layers

    def build(
        self,
        user_id: str,
        role: str | None = None,
        context_layers: set[str] | None = None,
    ) -> str:
        """Build full system prompt for a user (backward compatible).

        Parameters
        ----------
        user_id : str
            Target user ID.
        role : str, optional
            Override role name. Falls back to config default.
        context_layers : set[str], optional
            Allowed context layers from RBAC. None = all layers.
        """
        layers = self.build_layers(user_id, role, context_layers, mark_delivered=True)
        parts = [lr.content for lr in layers.values() if lr.content]
        return "\n\n---\n\n".join(parts)

    def get_context_stats(
        self,
        user_id: str,
        role: str | None = None,
        context_layers: set[str] | None = None,
    ) -> dict:
        """Measure each context layer's size without building the full prompt.

        Returns a dict with per-layer char/token counts and totals.
        """
        layers = self.build_layers(user_id, role, context_layers, mark_delivered=False)
        layer_stats = [
            {
                "layer": lr.name,
                "chars": lr.chars,
                "tokens": lr.tokens,
                "budget": lr.budget,
                "truncated": lr.truncated,
            }
            for lr in layers.values()
        ]
        return {
            "layers": layer_stats,
            "total_chars": sum(d["chars"] for d in layer_stats),
            "total_tokens": sum(d["tokens"] for d in layer_stats),
        }

    # ── Identity resolution ───────────────────────────────────

    def _get_identity(self) -> str:
        """Get identity prompt.

        Priority: prompt_template > system_prompt > profile AGENT.md > workspace/AGENT.md > persona config.
        """
        # Priority 0: custom prompt template file
        template = self._load_template()
        if template:
            return self._apply_persona_suffix(template)

        # Priority 1: explicit system_prompt in config
        if self.config.assistant.system_prompt:
            return self._apply_persona_suffix(self.config.assistant.system_prompt)

        # Priority 2: profile AGENT.md (from agents.yaml)
        profile_md = get_agent_md(self.profile)
        if profile_md and profile_md.strip():
            return self._apply_persona_suffix(profile_md.strip())

        # Priority 3: workspace/AGENT.md (direct fallback)
        agent_md = self.config.workspace_path / "AGENT.md"
        if agent_md.exists():
            content = agent_md.read_text(encoding="utf-8").strip()
            if content:
                return self._apply_persona_suffix(content)

        # Priority 4: build from persona config
        return self._build_persona_prompt()

    def _build_persona_prompt(self) -> str:
        """Build identity from persona config (fallback when no AGENT.md)."""
        persona = self.config.assistant.persona
        name = persona.name or self.config.assistant.name
        parts = [f"You are {name}, a helpful AI assistant."]
        if persona.tone:
            parts.append(f"Tone: {persona.tone}.")
        if persona.language:
            parts.append(f"Always respond in: {persona.language}.")
        if persona.constraints:
            parts.append("Constraints:")
            for c in persona.constraints:
                parts.append(f"- {c}")
        return "\n".join(parts)

    def _apply_persona_suffix(self, base: str) -> str:
        """Append persona constraints to existing identity if configured."""
        persona = self.config.assistant.persona
        if not persona.constraints:
            return base
        suffix = "\n\n## Additional Constraints\n\n" + "\n".join(
            f"- {c}" for c in persona.constraints
        )
        return base + suffix

    def _load_template(self) -> str | None:
        """Load custom prompt template file if configured."""
        path = self.config.assistant.prompt_template
        if not path:
            return None
        template_path = Path(path).expanduser().resolve()
        if not template_path.exists():
            return None
        raw = template_path.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        persona = self.config.assistant.persona
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        variables = {
            "name": persona.name or self.config.assistant.name,
            "tone": persona.tone,
            "language": persona.language,
            "datetime": now,
        }
        try:
            return raw.format_map(variables)
        except (KeyError, ValueError):
            return raw

    # ── Role resolution ───────────────────────────────────────

    def _get_role(self, role_name: str | None = None) -> str | None:
        """Get role description. Falls back to config default role."""
        roles = self.config.assistant.roles
        name = role_name or roles.default
        if not name:
            return None
        if name in roles.available:
            return f"Role: {name} — {roles.available[name]}"
        if roles.default:
            return f"Role: {roles.default}"
        return None

    # ── Helpers ───────────────────────────────────────────────

    @staticmethod
    def _truncate(text: str, token_budget: int) -> str:
        """Truncate text to approximate token budget (1 token ~ 4 chars)."""
        char_limit = token_budget * 4
        if len(text) <= char_limit:
            return text
        return text[:char_limit] + "\n\n[...truncated]"
