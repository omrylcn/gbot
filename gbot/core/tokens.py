"""Token measurement + budget fitting helpers.

Faz 22J-C — the wiki page system can produce pages up to 3000 tokens
each, the context budget grew from 1500 to 6000, and we're going to
make ongoing decisions about whether a block fits a sub-budget. The
old "len(text) // 4" heuristic was good enough when nothing was close
to a budget; now we want real numbers when we can get them.

Design:
- ``tiktoken`` is an optional dep. If installed, we use the OpenAI
  ``cl100k_base`` encoder by default. Anthropic / Gemini don't expose
  their tokenizer in tiktoken, but cl100k is within ~10% for English
  and Turkish — close enough for budget enforcement.
- If the package or encoder fails to import (offline tests, no
  internet for first-use download), we fall back to the same
  ``len(text) // 4`` heuristic the rest of the codebase used before.
  Behaviour stays identical; numbers just get noisier.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

_ENCODER: Any | None = None
_ENCODER_TRIED = False


def _get_encoder():
    """Lazy-load tiktoken's cl100k_base encoder, fall back to None on
    any import / network / FFI failure. Cached after first attempt."""
    global _ENCODER, _ENCODER_TRIED
    if _ENCODER_TRIED:
        return _ENCODER
    _ENCODER_TRIED = True
    try:
        import tiktoken  # type: ignore
    except ImportError:
        return None
    try:
        _ENCODER = tiktoken.get_encoding("cl100k_base")
    except Exception as e:  # pragma: no cover — first-call download fail
        logger.debug(f"tiktoken get_encoding failed, falling back: {e}")
        _ENCODER = None
    return _ENCODER


def count_tokens(text: str, *, model: str | None = None) -> int:  # noqa: ARG001
    """Best-effort token count for ``text``. ``model`` is accepted for
    future per-model encoders but currently unused — we use cl100k for
    everything since it's within ~10% across the providers we support.
    """
    if not text:
        return 0
    enc = _get_encoder()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:  # pragma: no cover — encoder edge case
            pass
    return len(text) // 4


def fit_to_budget(
    text: str, budget: int, *, model: str | None = None,
) -> str:  # noqa: ARG001
    """Return ``text`` trimmed to fit ``budget`` tokens.

    Strategy:
    1. If the whole thing already fits, return as-is.
    2. Otherwise binary-search by line boundary so we don't cut in the
       middle of a markdown bullet or citation. Keeps the first N
       lines whose combined token count is below the budget and tags
       the result with ``[...truncated]`` so the LLM knows.
    3. Never returns content larger than budget. Returns an empty
       string for budget ≤ 0.
    """
    if budget <= 0 or not text:
        return ""
    tokens = count_tokens(text)
    if tokens <= budget:
        return text

    lines = text.splitlines()
    if not lines:
        # No line boundaries — fall back to a character slice (the same
        # heuristic the legacy ``_truncate`` used).
        approx_chars = budget * 4
        return text[:approx_chars].rstrip() + "\n\n[...truncated]"

    # Binary search on the line index.
    lo, hi = 0, len(lines)
    best = ""
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = "\n".join(lines[:mid])
        if count_tokens(candidate) <= budget - 8:  # leave room for marker
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1

    if not best:
        # Even one line is too big — char-slice the first line.
        approx_chars = max(0, budget * 4 - 32)
        return lines[0][:approx_chars].rstrip() + "\n\n[...truncated]"

    if best == text:
        return text
    return best.rstrip() + "\n\n[...truncated]"


def is_tiktoken_available() -> bool:
    """Diagnostic — true if cl100k_base loaded successfully."""
    return _get_encoder() is not None
