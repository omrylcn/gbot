"""Shell tool — command execution with safety guards (adapted from nanobot)."""

from __future__ import annotations

import asyncio
import re

from langchain_core.tools import tool

from graphbot.core.config.schema import Config

# Deny patterns from nanobot — block destructive commands
DENY_PATTERNS: list[re.Pattern] = [
    re.compile(r"\brm\s+(-[rR]|-[rR]?f|-f?[rR])\b"),  # rm -rf, rm -r, rm -f
    re.compile(r"\bdel\s+/[fFqQ]\b"),  # Windows del /f /q
    re.compile(r"\brmdir\s+/[sS]\b"),  # Windows rmdir /s
    re.compile(r"\b(format|mkfs|diskpart)\b"),  # Disk format
    re.compile(r"\bdd\s+if="),  # dd disk copy
    re.compile(r">\s*/dev/sd"),  # Write to disk device
    re.compile(r"\b(shutdown|reboot|poweroff|halt)\b"),  # Power commands
    re.compile(r":\(\)\s*\{.*\}"),  # Fork bomb
]

MAX_OUTPUT = 10_000


def make_shell_tools(config: Config) -> list:
    """Create shell tools with safety guards."""
    timeout = config.tools.shell.timeout

    @tool
    async def exec_command(command: str, working_dir: str | None = None) -> str:
        """Execute a shell command. Dangerous commands (rm -rf, format, etc.) are blocked."""
        # Safety check
        for pattern in DENY_PATTERNS:
            if pattern.search(command):
                return f"Command blocked by safety filter: {command}"

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_dir,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )

            parts = []
            if stdout:
                out = stdout.decode("utf-8", errors="replace")
                parts.append(out)
            if stderr:
                err = stderr.decode("utf-8", errors="replace")
                parts.append(f"[stderr]\n{err}")

            output = "\n".join(parts)
            if len(output) > MAX_OUTPUT:
                output = output[:MAX_OUTPUT] + f"\n\n... truncated ({len(output)} chars)"

            exit_code = proc.returncode
            return f"[exit code: {exit_code}]\n{output}" if output else f"[exit code: {exit_code}]"

        except asyncio.TimeoutError:
            proc.kill()
            return f"Command timed out after {timeout}s: {command}"
        except Exception as e:
            return f"Execution error: {e}"

    return [exec_command]
