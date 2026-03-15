"""SkillLoader — discovers and loads SKILL.md files with YAML frontmatter."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml
from loguru import logger


@dataclass
class SkillMeta:
    """Parsed skill metadata from YAML frontmatter."""

    name: str
    description: str
    always: bool
    available: bool
    path: Path


class SkillLoader:
    """Loads skills from builtin and workspace directories.

    Skill format: {dir}/skills/{name}/SKILL.md
    Workspace skills override builtin skills with the same name.
    """

    def __init__(self, workspace: Path, builtin_dir: Path):
        self._workspace_skills = workspace / "skills"
        self._builtin_dir = builtin_dir

    def discover(self) -> list[SkillMeta]:
        """Find all available skills (builtin + workspace, workspace overrides)."""
        skills: dict[str, SkillMeta] = {}

        # 1. Builtin skills
        for meta in self._scan_dir(self._builtin_dir):
            skills[meta.name] = meta

        # 2. Workspace skills (override builtin)
        for meta in self._scan_dir(self._workspace_skills):
            skills[meta.name] = meta

        return sorted(skills.values(), key=lambda s: s.name)

    def load_content(self, name: str) -> str | None:
        """Load a skill's markdown body (frontmatter stripped)."""
        skill = self._find_skill(name)
        if skill is None:
            return None
        _, body = self._parse_frontmatter(skill.path)
        return body.strip() if body else None

    def get_always_on(self) -> list[SkillMeta]:
        """Return skills marked always=true with requirements met."""
        return [s for s in self.discover() if s.always and s.available]

    def build_index(self) -> str:
        """Build XML index of all discovered skills."""
        skills = self.discover()
        if not skills:
            return ""
        lines = ["<skills>"]
        for s in skills:
            avail = "true" if s.available else "false"
            lines.append(f'  <skill available="{avail}">')
            lines.append(f"    <name>{s.name}</name>")
            lines.append(f"    <description>{s.description}</description>")
            lines.append(f"    <path>{s.path}</path>")
            lines.append("  </skill>")
        lines.append("</skills>")
        return "\n".join(lines)

    def _find_skill(self, name: str) -> SkillMeta | None:
        """Find a skill by name (workspace priority)."""
        for s in self.discover():
            if s.name == name:
                return s
        return None

    def _scan_dir(self, base: Path) -> list[SkillMeta]:
        """Scan a directory for SKILL.md files."""
        results: list[SkillMeta] = []
        if not base.is_dir():
            return results
        for child in sorted(base.iterdir()):
            skill_file = child / "SKILL.md"
            if child.is_dir() and skill_file.exists():
                try:
                    fm, _ = self._parse_frontmatter(skill_file)
                    name = fm.get("name", child.name)
                    desc = fm.get("description", "")
                    always = fm.get("always", False)
                    available = self._check_requirements(fm.get("metadata", {}))
                    results.append(SkillMeta(
                        name=name,
                        description=desc,
                        always=always,
                        available=available,
                        path=skill_file,
                    ))
                except Exception as e:
                    logger.warning(f"Failed to parse skill {skill_file}: {e}")
        return results

    @staticmethod
    def _parse_frontmatter(path: Path) -> tuple[dict, str]:
        """Parse YAML frontmatter + markdown body from a SKILL.md file.

        Returns (frontmatter_dict, body_string).
        """
        content = path.read_text(encoding="utf-8")
        if not content.startswith("---"):
            return {}, content

        parts = content.split("---", 2)
        if len(parts) < 3:
            return {}, content

        fm = yaml.safe_load(parts[1]) or {}
        body = parts[2]
        return fm, body

    @staticmethod
    def _check_requirements(metadata: dict) -> bool:
        """Check if skill requirements (bins, env) are met."""
        requires = metadata.get("requires", {})

        for bin_name in requires.get("bins", []):
            if not shutil.which(bin_name):
                return False

        for env_var in requires.get("env", []):
            if not os.environ.get(env_var):
                return False

        return True
