"""ContextBuilder — assembles system prompt from SQLite + workspace files."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from graphbot.agent.skills.loader import SkillLoader
from graphbot.core.config.schema import Config
from graphbot.memory.store import MemoryStore


class ContextBuilder:
    """
    Builds layered system prompt (~4k token budget):
      1. Identity (AGENT.md or config.system_prompt)
      2. Agent memory (agent_memory table)
      3. User context (notes, activities, favorites, preferences)
      4. Previous session summary
      5. Active skills (always-on, full content)
      6. Skills index (all skills, name + description)
    """

    def __init__(self, config: Config, db: MemoryStore):
        self.config = config
        self.db = db
        self.skills = SkillLoader(
            workspace=config.workspace_path,
            builtin_dir=Path(__file__).parent / "skills" / "builtin",
        )

    def build(self, user_id: str) -> str:
        """Build full system prompt for a user."""
        parts: list[str] = []

        # 1. Identity
        identity = self._get_identity()
        if identity:
            parts.append(identity)

        # 2. Agent memory
        memory = self.db.read_memory("long_term")
        if memory:
            parts.append(f"# Agent Memory\n\n{memory}")

        # 3. User context
        user_ctx = self.db.get_user_context(user_id)
        if user_ctx:
            parts.append(f"# User Context\n\n{user_ctx}")

        # 4. Previous session summary
        summary = self.db.get_last_session_summary(user_id)
        if summary:
            parts.append(f"# Previous Conversation\n\n{summary}")

        # 5. Active skills (always-on, full content)
        always_on = self.skills.get_always_on()
        if always_on:
            skill_texts = [self.skills.load_content(s.name) for s in always_on]
            active = "\n\n---\n\n".join(t for t in skill_texts if t)
            if active:
                parts.append(f"# Active Skills\n\n{active}")

        # 6. Skills index
        index = self.skills.build_index()
        if index:
            parts.append(
                "# Available Skills\n\n"
                "Use read_file to load a skill's full instructions when needed.\n\n"
                + index
            )

        return "\n\n---\n\n".join(parts)

    def _get_identity(self) -> str:
        """Get identity: config.system_prompt > AGENT.md > default."""
        # Priority 1: explicit system_prompt in config
        if self.config.assistant.system_prompt:
            return self.config.assistant.system_prompt

        # Priority 2: workspace/AGENT.md
        agent_md = self.config.workspace_path / "AGENT.md"
        if agent_md.exists():
            content = agent_md.read_text(encoding="utf-8").strip()
            if content:
                return content

        # Priority 3: built-in default
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        name = self.config.assistant.name
        return (
            f"You are {name}, a helpful AI assistant.\n"
            f"Current time: {now}\n"
            f"Be helpful, accurate, and concise."
        )
