"""ContextBuilder — assembles system prompt from SQLite + workspace files."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from graphbot.agent.skills.loader import SkillLoader
from graphbot.core.config.schema import Config
from graphbot.memory.store import MemoryStore


class ContextBuilder:
    """
    Builds layered system prompt with configurable persona, roles, and token budgets.

    Layers:
      1. Identity (prompt_template > system_prompt > AGENT.md > persona config)
      2. Runtime info (user_id, datetime)
      3. Current role (if configured)
      4. Agent memory (agent_memory table)
      5. User context (notes, activities, favorites, preferences)
      6. Previous session summary
      7. Active skills (always-on, full content)
      8. Skills index (all skills, name + description)
    """

    def __init__(self, config: Config, db: MemoryStore):
        self.config = config
        self.db = db
        self.skills = SkillLoader(
            workspace=config.workspace_path,
            builtin_dir=Path(__file__).parent / "skills" / "builtin",
        )

    def build(
        self,
        user_id: str,
        role: str | None = None,
        context_layers: set[str] | None = None,
    ) -> str:
        """Build full system prompt for a user.

        Parameters
        ----------
        user_id : str
            Target user ID.
        role : str, optional
            Override role name. Falls back to config default.
        context_layers : set[str], optional
            Allowed context layers from RBAC. None = all layers.
        """
        parts: list[str] = []
        priorities = self.config.assistant.context_priorities

        def _allowed(layer: str) -> bool:
            return context_layers is None or layer in context_layers

        # 1. Identity (always included)
        if _allowed("identity"):
            identity = self._get_identity()
            if identity:
                parts.append(self._truncate(identity, priorities.identity))

        # 2. Runtime info (user_id, datetime)
        if _allowed("runtime"):
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            user = self.db.get_user(user_id)
            user_name = user["name"] if user and user.get("name") else user_id
            parts.append(
                f"# Runtime\n\n"
                f"- Current user_id: {user_id}\n"
                f"- Current user_name: {user_name}\n"
                f"- Current time: {now}\n"
                f"- Use this user_id when calling tools that require it."
            )

        # 3. Current role
        if _allowed("role"):
            role_text = self._get_role(role)
            if role_text:
                parts.append(f"# Current Role\n\n{role_text}")

        # 4. Agent memory
        if _allowed("agent_memory"):
            memory = self.db.read_memory("long_term")
            if memory:
                parts.append(
                    self._truncate(f"# Agent Memory\n\n{memory}", priorities.agent_memory)
                )

        # 5. User context
        if _allowed("user_context"):
            user_ctx = self.db.get_user_context(user_id)
            if user_ctx:
                parts.append(
                    self._truncate(
                        f"# User Context\n\n{user_ctx}", priorities.user_context
                    )
                )

        # 6. Undelivered system events (background notifications)
        if _allowed("events"):
            events = self.db.get_undelivered_events(user_id, limit=5)
            if events:
                lines = [
                    f"- [{e['source']}] {e['payload']}" for e in events
                ]
                parts.append(
                    "# Background Notifications\n\n" + "\n".join(lines)
                )
                self.db.mark_events_delivered([e["id"] for e in events])

        # 7. Previous session summary
        if _allowed("session_summary"):
            summary = self.db.get_last_session_summary(user_id)
            if summary:
                parts.append(
                    self._truncate(
                        f"# Previous Conversation\n\n{summary}",
                        priorities.session_summary,
                    )
                )

        # 8. Active skills (always-on, full content)
        if _allowed("skills"):
            always_on = self.skills.get_always_on()
            if always_on:
                skill_texts = [self.skills.load_content(s.name) for s in always_on]
                active = "\n\n---\n\n".join(t for t in skill_texts if t)
                if active:
                    parts.append(
                        self._truncate(
                            f"# Active Skills\n\n{active}", priorities.skills
                        )
                    )

            # Skills index
            index = self.skills.build_index()
            if index:
                parts.append(
                    "# Available Skills\n\n"
                    "Use read_file to load a skill's full instructions when needed.\n\n"
                    + index
                )

        return "\n\n---\n\n".join(parts)

    # ── Identity resolution ───────────────────────────────────

    def _get_identity(self) -> str:
        """Get identity prompt.

        Priority: prompt_template file > system_prompt > AGENT.md > persona config.
        """
        # Priority 0: custom prompt template file
        template = self._load_template()
        if template:
            return self._apply_persona_suffix(template)

        # Priority 1: explicit system_prompt in config
        if self.config.assistant.system_prompt:
            return self._apply_persona_suffix(self.config.assistant.system_prompt)

        # Priority 2: workspace/AGENT.md
        agent_md = self.config.workspace_path / "AGENT.md"
        if agent_md.exists():
            content = agent_md.read_text(encoding="utf-8").strip()
            if content:
                return self._apply_persona_suffix(content)

        # Priority 3: build from persona config
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
        # Render simple {variable} placeholders
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

    def get_context_stats(
        self,
        user_id: str,
        role: str | None = None,
        context_layers: set[str] | None = None,
    ) -> dict:
        """Measure each context layer's size without building the full prompt.

        Returns a dict with per-layer char/token counts and totals.
        """
        priorities = self.config.assistant.context_priorities
        layers: list[dict] = []

        def _allowed(layer: str) -> bool:
            return context_layers is None or layer in context_layers

        def _add(name: str, text: str, budget: int = 0) -> None:
            chars = len(text)
            tokens_approx = chars // 4
            layers.append({
                "layer": name,
                "chars": chars,
                "tokens": tokens_approx,
                "budget": budget,
                "truncated": chars > budget * 4 if budget else False,
            })

        if _allowed("identity"):
            identity = self._get_identity()
            _add("identity", identity or "", priorities.identity)

        if _allowed("runtime"):
            _add("runtime", "~runtime~", 0)  # small, constant

        if _allowed("role"):
            role_text = self._get_role(role) or ""
            _add("role", role_text, 0)

        if _allowed("agent_memory"):
            memory = self.db.read_memory("long_term") or ""
            _add("agent_memory", memory, priorities.agent_memory)

        if _allowed("user_context"):
            user_ctx = self.db.get_user_context(user_id) or ""
            _add("user_context", user_ctx, priorities.user_context)

        if _allowed("events"):
            events = self.db.get_undelivered_events(user_id, limit=5)
            text = "\n".join(e.get("payload", "") for e in events) if events else ""
            _add("events", text, 0)

        if _allowed("session_summary"):
            summary = self.db.get_last_session_summary(user_id) or ""
            _add("session_summary", summary, priorities.session_summary)

        if _allowed("skills"):
            always_on = self.skills.get_always_on()
            skill_texts = [self.skills.load_content(s.name) for s in always_on]
            active = "\n".join(t for t in skill_texts if t)
            index = self.skills.build_index() or ""
            _add("skills", active + index, priorities.skills)

        total_chars = sum(layer["chars"] for layer in layers)
        total_tokens = sum(layer["tokens"] for layer in layers)

        return {
            "layers": layers,
            "total_chars": total_chars,
            "total_tokens": total_tokens,
        }

    # ── Helpers ───────────────────────────────────────────────

    @staticmethod
    def _truncate(text: str, token_budget: int) -> str:
        """Truncate text to approximate token budget (1 token ~ 4 chars)."""
        char_limit = token_budget * 4
        if len(text) <= char_limit:
            return text
        return text[:char_limit] + "\n\n[...truncated]"
