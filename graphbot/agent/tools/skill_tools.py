"""Skill tools — load_skill for progressive disclosure."""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool

from graphbot.agent.skills.loader import SkillLoader
from graphbot.core.config.schema import Config


def make_skill_tools(config: Config) -> list:
    """Create skill-related tools."""
    workspace = config.workspace_path
    builtin_dir = Path(__file__).parent.parent / "skills" / "builtin"
    loader = SkillLoader(workspace=workspace, builtin_dir=builtin_dir)

    @tool
    def load_skill(skill_name: str) -> str:
        """Load detailed instructions for a specific skill by name.

        Use this when you need step-by-step guidance for a task type
        (e.g. scheduling, weather). The skill index in your context
        shows available skill names.

        Parameters
        ----------
        skill_name : str
            Name of the skill to load (e.g. "scheduling", "weather").

        Returns
        -------
        str
            Full skill content with detailed instructions.
        """
        content = loader.load_content(skill_name)
        if content is None:
            available = [s.name for s in loader.discover()]
            return f"Skill '{skill_name}' not found. Available: {', '.join(available)}"
        return content

    return [load_skill]
