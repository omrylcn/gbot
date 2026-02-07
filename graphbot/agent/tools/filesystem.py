"""Filesystem tools — read, write, edit, list (adapted from nanobot)."""

from __future__ import annotations

import os
from pathlib import Path

from langchain_core.tools import tool

from graphbot.core.config.schema import Config


def make_filesystem_tools(config: Config) -> list:
    """Create filesystem tools sandboxed to workspace directory."""
    workspace = Path(config.assistant.workspace).resolve()

    def _resolve(path: str) -> Path:
        """Resolve and validate path is within workspace."""
        resolved = Path(path).expanduser().resolve()
        # Allow workspace and its children
        if not str(resolved).startswith(str(workspace)):
            raise PermissionError(
                f"Access denied: path '{path}' is outside workspace '{workspace}'"
            )
        return resolved

    @tool
    def read_file(path: str) -> str:
        """Read a text file from the workspace."""
        try:
            p = _resolve(path)
            if not p.exists():
                return f"File not found: {path}"
            if not p.is_file():
                return f"Not a file: {path}"
            content = p.read_text(encoding="utf-8")
            if len(content) > 50_000:
                return content[:50_000] + f"\n\n... truncated ({len(content)} chars total)"
            return content
        except PermissionError as e:
            return str(e)

    @tool
    def write_file(path: str, content: str) -> str:
        """Write content to a file in the workspace. Creates parent directories if needed."""
        try:
            p = _resolve(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return f"Written {len(content)} bytes to {path}"
        except PermissionError as e:
            return str(e)

    @tool
    def edit_file(path: str, old_text: str, new_text: str) -> str:
        """Replace exact text in a file. Fails if old_text not found or appears multiple times."""
        try:
            p = _resolve(path)
            if not p.exists():
                return f"File not found: {path}"
            content = p.read_text(encoding="utf-8")
            count = content.count(old_text)
            if count == 0:
                return "old_text not found in file."
            if count > 1:
                return f"old_text found {count} times — must be unique. Provide more context."
            new_content = content.replace(old_text, new_text, 1)
            p.write_text(new_content, encoding="utf-8")
            return "Edit applied successfully."
        except PermissionError as e:
            return str(e)

    @tool
    def list_dir(path: str = ".") -> str:
        """List contents of a directory in the workspace."""
        try:
            p = _resolve(path) if path != "." else workspace
            if not p.exists():
                return f"Directory not found: {path}"
            if not p.is_dir():
                return f"Not a directory: {path}"
            entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name))
            lines = []
            for entry in entries:
                prefix = "[DIR]" if entry.is_dir() else f"[{_human_size(entry.stat().st_size)}]"
                lines.append(f"  {prefix}  {entry.name}")
            return f"{p}:\n" + "\n".join(lines) if lines else f"{p}: (empty)"
        except PermissionError as e:
            return str(e)

    return [read_file, write_file, edit_file, list_dir]


def _human_size(size: int) -> str:
    """Convert bytes to human-readable size."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"
