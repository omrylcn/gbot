"""HeartbeatService — periodic wake-up that checks HEARTBEAT.md."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from graphbot.core.config.schema import Config

if TYPE_CHECKING:
    from graphbot.agent.runner import GraphRunner

HEARTBEAT_PROMPT = (
    "A periodic heartbeat has triggered. "
    "Read the HEARTBEAT.md file in your workspace and follow any instructions there. "
    "If there are no actionable tasks, respond with HEARTBEAT_OK."
)


class HeartbeatService:
    """Periodic wake-up service that reads HEARTBEAT.md and triggers the agent."""

    def __init__(self, config: Config, runner: GraphRunner):
        self.runner = runner
        self.workspace = Path(config.assistant.workspace).resolve()
        self.interval_s = config.background.heartbeat.interval_s
        self.enabled = config.background.heartbeat.enabled
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the heartbeat loop."""
        if not self.enabled:
            logger.debug("HeartbeatService disabled")
            return
        self._running = True
        logger.info(f"HeartbeatService started (interval={self.interval_s}s)")
        while self._running:
            await asyncio.sleep(self.interval_s)
            if not self._running:
                break
            await self._tick()

    def stop(self) -> None:
        """Stop the heartbeat loop."""
        self._running = False
        logger.info("HeartbeatService stopped")

    async def _tick(self) -> None:
        """Check HEARTBEAT.md and trigger agent if needed."""
        content = self._read_heartbeat_file()
        if not content or _is_empty_content(content):
            logger.debug("Heartbeat: nothing to do")
            return

        logger.info("Heartbeat: actionable content found, triggering agent")
        try:
            await self.runner.process(
                user_id="system",
                channel="heartbeat",
                message=HEARTBEAT_PROMPT,
            )
        except Exception as e:
            logger.error(f"Heartbeat execution error: {e}")

    def _read_heartbeat_file(self) -> str:
        """Read HEARTBEAT.md from workspace."""
        path = self.workspace / "HEARTBEAT.md"
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8").strip()
        except Exception:
            return ""


def _is_empty_content(content: str) -> bool:
    """Check if HEARTBEAT.md has only non-actionable content."""
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        if line.startswith("<!--") and line.endswith("-->"):
            continue
        if line == "- [ ]":
            continue
        return False
    return True
