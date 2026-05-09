"""``kind: python`` scoring rule — restricted Python expression escape hatch.

YAML usage::

    scoring:
      - kind: python
        expr: |
          # Available locals: text, tool_calls, case, call (CallResult)
          # Must return {"score": float in [0,1], "detail": {...}}
          data = json.loads(text) if text.strip() else {}
          hits = sum(1 for k in ["a", "b", "c"] if k in data)
          return {"score": hits / 3.0, "detail": {"hits": hits}}

The expression body runs with a curated globals dict — no
``__import__``, no ``os/sys/subprocess``. AST is inspected before
execution and rejected on any reference to forbidden names. The body
is wrapped in a synthetic function so ``return`` works naturally.

This is an escape hatch, not a primary scoring path — most cases
should use a built-in ``kind`` if one fits. Reach for ``python``
when the rule is a one-off that doesn't justify a new built-in.
"""

from __future__ import annotations

import ast
import json
import math
import re
import textwrap
from typing import Any

from gbot_eval.scoring import register
from gbot_eval.scoring.base import ScoringContext, ScoringResult

# Locals exposed to the expression body. Adding stdlib here is fine —
# adding ``os``, ``sys``, ``subprocess``, ``socket`` is not.
_SAFE_GLOBALS: dict[str, Any] = {
    "__builtins__": {
        "abs": abs, "all": all, "any": any, "bool": bool,
        "dict": dict, "enumerate": enumerate, "filter": filter,
        "float": float, "int": int, "isinstance": isinstance,
        "len": len, "list": list, "map": map, "max": max,
        "min": min, "range": range, "round": round, "set": set,
        "sorted": sorted, "str": str, "sum": sum, "tuple": tuple,
        "zip": zip,
        # Useful exceptions for try/except inside expressions
        "ValueError": ValueError, "KeyError": KeyError,
        "TypeError": TypeError, "IndexError": IndexError,
    },
    "json": json,
    "re": re,
    "math": math,
}

_FORBIDDEN_NAMES = {
    "__import__", "exec", "eval", "compile", "open",
    "globals", "locals", "vars", "getattr", "setattr",
    "delattr", "hasattr", "input", "exit", "quit",
}


def _validate_ast(tree: ast.AST) -> str | None:
    """Walk the AST and reject obviously dangerous constructs.

    Catches ``import`` statements, attribute access onto risky names,
    and bare references to forbidden builtins. Not a full sandbox —
    a determined caller can still misbehave. Good enough for a local
    dev-tool eval suite.
    """
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return f"import disallowed: {ast.dump(node)[:80]}"
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            return f"forbidden name: {node.id}"
        if isinstance(node, ast.Attribute):
            # Reject .__class__, .__bases__, .__mro__ probing
            if node.attr.startswith("__") and node.attr.endswith("__"):
                return f"dunder attribute access disallowed: {node.attr}"
    return None


@register("python")
def _python_expr(rule: dict, ctx: ScoringContext) -> ScoringResult:
    expr_src = rule.get("expr")
    if not expr_src:
        return ScoringResult(score=0.0, error="missing 'expr' field")

    body = textwrap.dedent(expr_src).strip()
    func_src = "def __scorer(text, tool_calls, case, call):\n" + textwrap.indent(
        body, "    "
    )
    try:
        tree = ast.parse(func_src, mode="exec")
    except SyntaxError as e:
        return ScoringResult(score=0.0, error=f"expr syntax error: {e}")

    err = _validate_ast(tree)
    if err:
        return ScoringResult(score=0.0, error=err)

    locals_dict: dict[str, Any] = {}
    try:
        exec(  # noqa: S102 — sandboxed via _SAFE_GLOBALS + AST validation
            compile(tree, "<gbot-eval expr>", "exec"),
            dict(_SAFE_GLOBALS),
            locals_dict,
        )
        scorer = locals_dict["__scorer"]
        result = scorer(ctx.text, ctx.tool_calls, ctx.case, ctx.call)
    except Exception as e:
        return ScoringResult(score=0.0, error=f"expr runtime error: {e}")

    if not isinstance(result, dict):
        return ScoringResult(
            score=0.0,
            error=f"expr must return a dict, got {type(result).__name__}",
        )
    score = float(result.get("score", 0.0))
    score = max(0.0, min(1.0, score))
    return ScoringResult(score=score, detail=result.get("detail", {}))
