"""GBot LLM evaluation framework — standalone CLI tool.

Measures the LLM-quality dimensions GBot depends on:

* Memory pipeline (extraction, AUDN decision, entity-page compile)
* Agent surface (delegation, tool calling, structured output, instruction
  following)
* Stress (long-context fidelity)

Cross-cutting captures: tokens (in/out), USD cost, latency p50/p95.

Entry point: ``gbot-eval`` (registered via ``pyproject.toml``).
"""

from gbot_eval.__version__ import __version__

__all__ = ["__version__"]
