"""Suite runner contract.

A runner bridges a YAML suite definition to LLM call sequences plus
scoring. Different runners exist because suites differ in shape:
``chat_completion`` runs a single user prompt; ``memory_extraction``
sets up a MemoryService; ``multi_turn`` cycles through turns.

YAML's ``runner: <name>`` field selects which runner handles each
suite. Built-in runners register themselves via ``@register("name")``.
"""

from __future__ import annotations

from typing import Protocol

from gbot_eval.suites.base import CaseResult


class Runner(Protocol):
    name: str

    async def run_case(
        self,
        case: dict,
        suite_config: dict,
        model: str,
    ) -> CaseResult: ...
