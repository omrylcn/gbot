"""Built-in scoring rule handlers.

Each handler is registered with a ``kind`` name and returns a
``ScoringResult``. Pure functions — no LLM calls, no I/O. The ``judge``
handler (in ``judge.py``) is the only stateful exception.
"""

from __future__ import annotations

import json
import re
from typing import Any

from gbot_eval.scoring import register
from gbot_eval.scoring.base import ScoringContext, ScoringResult

# ── Turkish-aware fold ──────────────────────────────────────────

_TR_FOLD = str.maketrans({
    "ı": "i", "İ": "i", "i": "i",
    "ş": "s", "Ş": "s",
    "ç": "c", "Ç": "c",
    "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u",
    "ö": "o", "Ö": "o",
})


def _fold(s: str, mode: str | None = None) -> str:
    """Lowercase + optional Turkish→ASCII fold."""
    if mode == "turkish":
        return s.translate(_TR_FOLD).lower()
    return s.lower() if mode == "lower" else s.lower()


# ── Regex / substring ───────────────────────────────────────────


@register("regex_match")
def _regex_match(rule: dict, ctx: ScoringContext) -> ScoringResult:
    pattern = rule["pattern"]
    flags = _parse_flags(rule.get("flags", ""))
    ok = bool(re.search(pattern, ctx.text, flags))
    return ScoringResult(
        score=1.0 if ok else 0.0,
        detail={"pattern": pattern, "ok": ok},
    )


@register("regex_not_match")
def _regex_not_match(rule: dict, ctx: ScoringContext) -> ScoringResult:
    pattern = rule["pattern"]
    flags = _parse_flags(rule.get("flags", ""))
    ok = not re.search(pattern, ctx.text, flags)
    return ScoringResult(
        score=1.0 if ok else 0.0,
        detail={"pattern": pattern, "ok": ok},
    )


@register("substring_any")
def _substring_any(rule: dict, ctx: ScoringContext) -> ScoringResult:
    fold = rule.get("fold")
    text = _fold(ctx.text, fold)
    values = [_fold(v, fold) for v in rule["values"]]
    matched = next((v for v in values if v in text), None)
    return ScoringResult(
        score=1.0 if matched else 0.0,
        detail={"matched": matched, "ok": matched is not None},
    )


@register("substring_all")
def _substring_all(rule: dict, ctx: ScoringContext) -> ScoringResult:
    fold = rule.get("fold")
    text = _fold(ctx.text, fold)
    values = [_fold(v, fold) for v in rule["values"]]
    missing = [v for v in values if v not in text]
    return ScoringResult(
        score=0.0 if missing else 1.0,
        detail={"missing": missing, "ok": not missing},
    )


@register("substring_none")
def _substring_none(rule: dict, ctx: ScoringContext) -> ScoringResult:
    """Forbidden substrings — score 1 iff none of them appear."""
    fold = rule.get("fold")
    text = _fold(ctx.text, fold)
    values = [_fold(v, fold) for v in rule["values"]]
    found = [v for v in values if v in text]
    return ScoringResult(
        score=0.0 if found else 1.0,
        detail={"forbidden_hit": found, "ok": not found},
    )


# ── Tool calling ────────────────────────────────────────────────


@register("tool_called")
def _tool_called(rule: dict, ctx: ScoringContext) -> ScoringResult:
    expected = rule["expected"]
    called = [c.get("name") for c in ctx.tool_calls]
    ok = expected in called
    return ScoringResult(
        score=1.0 if ok else 0.0,
        detail={"expected": expected, "called": called, "ok": ok},
    )


@register("tool_not_called")
def _tool_not_called(rule: dict, ctx: ScoringContext) -> ScoringResult:
    forbidden = rule.get("forbidden") or rule.get("name")
    called = [c.get("name") for c in ctx.tool_calls]
    ok = forbidden not in called
    return ScoringResult(
        score=1.0 if ok else 0.0,
        detail={"forbidden": forbidden, "called": called, "ok": ok},
    )


@register("no_tool_call")
def _no_tool_call(rule: dict, ctx: ScoringContext) -> ScoringResult:
    ok = not ctx.tool_calls
    return ScoringResult(
        score=1.0 if ok else 0.0,
        detail={"called": [c.get("name") for c in ctx.tool_calls], "ok": ok},
    )


@register("tool_count_min")
def _tool_count_min(rule: dict, ctx: ScoringContext) -> ScoringResult:
    n_min = int(rule["min"])
    n = len(ctx.tool_calls)
    ok = n >= n_min
    return ScoringResult(
        score=1.0 if ok else 0.0,
        detail={"min": n_min, "got": n, "ok": ok},
    )


@register("required_args")
def _required_args(rule: dict, ctx: ScoringContext) -> ScoringResult:
    tname = rule["tool"]
    keys = rule["keys"]
    for tc in ctx.tool_calls:
        if tc.get("name") != tname:
            continue
        args = tc.get("args") or {}
        missing = [k for k in keys if k not in args]
        return ScoringResult(
            score=0.0 if missing else 1.0,
            detail={"tool": tname, "missing": missing, "ok": not missing},
        )
    return ScoringResult(
        score=0.0,
        detail={"tool": tname, "issue": "tool not called"},
    )


@register("arg_substring_any")
def _arg_substring_any(rule: dict, ctx: ScoringContext) -> ScoringResult:
    tname = rule["tool"]
    arg = rule["arg"]
    fold = rule.get("fold")
    values = [_fold(v, fold) for v in rule["values"]]
    for tc in ctx.tool_calls:
        if tc.get("name") != tname:
            continue
        actual = _fold(str((tc.get("args") or {}).get(arg, "")), fold)
        matched = next((v for v in values if v in actual), None)
        return ScoringResult(
            score=1.0 if matched else 0.0,
            detail={
                "tool": tname,
                "arg": arg,
                "actual": str((tc.get("args") or {}).get(arg, ""))[:80],
                "matched": matched,
                "ok": matched is not None,
            },
        )
    return ScoringResult(
        score=0.0,
        detail={"tool": tname, "issue": "tool not called"},
    )


# ── JSON schema ─────────────────────────────────────────────────


def _parse_json(text: str) -> tuple[Any | None, str | None]:
    text = (text or "").strip()
    if not text:
        return None, "empty"
    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None, "no_json_block"
        try:
            return json.loads(match.group(0)), None
        except json.JSONDecodeError as e:
            return None, f"decode_error: {e}"


@register("json_valid")
def _json_valid(rule: dict, ctx: ScoringContext) -> ScoringResult:
    data, err = _parse_json(ctx.text)
    ok = data is not None
    return ScoringResult(
        score=1.0 if ok else 0.0,
        detail={"ok": ok, "err": err},
    )


@register("json_keys")
def _json_keys(rule: dict, ctx: ScoringContext) -> ScoringResult:
    data, err = _parse_json(ctx.text)
    if not isinstance(data, dict):
        return ScoringResult(
            score=0.0, detail={"ok": False, "err": err or "not_object"}
        )
    missing = [k for k in rule["keys"] if k not in data]
    return ScoringResult(
        score=0.0 if missing else 1.0,
        detail={"missing": missing, "ok": not missing},
    )


_TYPE_CHECKERS = {
    "str": lambda v: isinstance(v, str),
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "float": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "bool": lambda v: isinstance(v, bool),
    "dict": lambda v: isinstance(v, dict),
    "list": lambda v: isinstance(v, list),
}


@register("json_types")
def _json_types(rule: dict, ctx: ScoringContext) -> ScoringResult:
    data, err = _parse_json(ctx.text)
    if not isinstance(data, dict):
        return ScoringResult(
            score=0.0, detail={"ok": False, "err": err or "not_object"}
        )
    bad = []
    for k, t in rule["types"].items():
        check = _TYPE_CHECKERS.get(t, lambda v: True)
        if k in data and not check(data[k]):
            bad.append({"key": k, "want": t, "got": type(data[k]).__name__})
    return ScoringResult(
        score=0.0 if bad else 1.0,
        detail={"bad": bad, "ok": not bad},
    )


@register("json_array_min")
def _json_array_min(rule: dict, ctx: ScoringContext) -> ScoringResult:
    data, err = _parse_json(ctx.text)
    if not isinstance(data, dict):
        return ScoringResult(
            score=0.0, detail={"ok": False, "err": err or "not_object"}
        )
    field = rule["field"]
    n_min = int(rule["min"])
    v = data.get(field)
    ok = isinstance(v, list) and len(v) >= n_min
    return ScoringResult(
        score=1.0 if ok else 0.0,
        detail={
            "field": field,
            "min": n_min,
            "got": len(v) if isinstance(v, list) else None,
            "ok": ok,
        },
    )


@register("json_nested_keys")
def _json_nested_keys(rule: dict, ctx: ScoringContext) -> ScoringResult:
    data, err = _parse_json(ctx.text)
    if not isinstance(data, dict):
        return ScoringResult(
            score=0.0, detail={"ok": False, "err": err or "not_object"}
        )
    parent = rule["parent"]
    children = rule["children"]
    nested = data.get(parent)
    if not isinstance(nested, dict):
        return ScoringResult(
            score=0.0,
            detail={"parent": parent, "ok": False, "issue": "not_object"},
        )
    missing = [c for c in children if c not in nested]
    return ScoringResult(
        score=0.0 if missing else 1.0,
        detail={"parent": parent, "missing": missing, "ok": not missing},
    )


# ── Format adherence ────────────────────────────────────────────

_BULLET_LINE = re.compile(r"^\s*[-*•]\s+", re.MULTILINE)
_NUMBERED_LINE = re.compile(r"^\s*\d+\.\s+", re.MULTILINE)
_SENTENCE_END = re.compile(r"[.!?]+(?:\s|$)")


@register("bullet_count")
def _bullet_count(rule: dict, ctx: ScoringContext) -> ScoringResult:
    n = len(_BULLET_LINE.findall(ctx.text))
    if "exact" in rule:
        ok = n == int(rule["exact"])
    else:
        n_min = int(rule.get("min", 0))
        n_max = int(rule.get("max", 999))
        ok = n_min <= n <= n_max
    return ScoringResult(
        score=1.0 if ok else 0.0,
        detail={"got": n, "ok": ok},
    )


@register("numbered_list")
def _numbered_list(rule: dict, ctx: ScoringContext) -> ScoringResult:
    n_min = int(rule.get("min", 1))
    n = len(_NUMBERED_LINE.findall(ctx.text))
    ok = n >= n_min
    return ScoringResult(
        score=1.0 if ok else 0.0,
        detail={"min": n_min, "got": n, "ok": ok},
    )


@register("word_count")
def _word_count(rule: dict, ctx: ScoringContext) -> ScoringResult:
    n = len(ctx.text.split())
    n_max = int(rule.get("max", 1_000_000))
    n_min = int(rule.get("min", 0))
    ok = n_min <= n <= n_max
    return ScoringResult(
        score=1.0 if ok else 0.0,
        detail={"min": n_min, "max": n_max, "got": n, "ok": ok},
    )


@register("sentence_count")
def _sentence_count(rule: dict, ctx: ScoringContext) -> ScoringResult:
    n = len(_SENTENCE_END.findall(ctx.text))
    n_max = int(rule.get("max", 1_000_000))
    n_min = int(rule.get("min", 0))
    ok = n_min <= n <= n_max
    return ScoringResult(
        score=1.0 if ok else 0.0,
        detail={"min": n_min, "max": n_max, "got": n, "ok": ok},
    )


# ── Helpers ─────────────────────────────────────────────────────


def _parse_flags(spec: str | int) -> int:
    if isinstance(spec, int):
        return spec
    flags = 0
    for ch in (spec or "").upper():
        if ch == "I":
            flags |= re.IGNORECASE
        elif ch == "M":
            flags |= re.MULTILINE
        elif ch == "S":
            flags |= re.DOTALL
    return flags
