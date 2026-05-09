"""Metrics for the LLM eval suite (Faz 22E Step 5).

Three families, one per pipeline call:

* Extraction — keyword recall over expected facts, relation recall.
* AUDN — exact-match accuracy on action; per-action breakdown.
* Page compile — citation correctness, keyword coverage, hallucination
  flags, length bounds.

These functions are intentionally simple and side-effect free so they
can be reused across tests and ad-hoc scripts.
"""

from __future__ import annotations

import re
from typing import Any


# ── Extraction ──────────────────────────────────────────────────


def extraction_recall(
    expected_facts: list[dict[str, Any]],
    extracted_facts: list[dict[str, Any]],
) -> float:
    """Fraction of expected facts whose keywords are present in some
    extracted fact's content. Case-insensitive substring match.
    """
    if not expected_facts:
        return 1.0
    extracted_lower = [
        (f.get("content") or "").lower() for f in extracted_facts
    ]
    hit = 0
    for exp in expected_facts:
        keywords = [k.lower() for k in exp.get("content_keywords", [])]
        if not keywords:
            continue
        if any(all(k in c for k in keywords) for c in extracted_lower):
            hit += 1
    return hit / len(expected_facts)


def relation_recall(
    expected_relations: list[dict[str, Any]],
    extracted_relations: list[dict[str, Any]],
) -> float:
    """Fraction of expected relations whose source/relation/target keywords
    appear in some extracted relation. Loose match — substring on each
    of the three fields.
    """
    if not expected_relations:
        return 1.0

    def _norm(rel: dict) -> tuple[str, str, str]:
        return (
            (rel.get("source") or rel.get("source_keyword") or "").lower(),
            (rel.get("relation") or "").lower(),
            (rel.get("target") or rel.get("target_keyword") or "").lower(),
        )

    extracted_norm = [_norm(r) for r in extracted_relations]
    hit = 0
    for exp in expected_relations:
        s, r, t = _norm(exp)
        for es, er, et in extracted_norm:
            if s in es and r in er and t in et:
                hit += 1
                break
    return hit / len(expected_relations)


def category_accuracy(
    expected_facts: list[dict[str, Any]],
    extracted_facts: list[dict[str, Any]],
) -> float:
    """Of the expected facts that we successfully matched by keyword,
    how many also had the correct ``category`` set.
    """
    if not expected_facts:
        return 1.0
    matched = 0
    correct = 0
    for exp in expected_facts:
        keywords = [k.lower() for k in exp.get("content_keywords", [])]
        if not keywords:
            continue
        for f in extracted_facts:
            content = (f.get("content") or "").lower()
            if all(k in content for k in keywords):
                matched += 1
                if (f.get("category") or "").lower() == (exp.get("category") or "").lower():
                    correct += 1
                break
    return correct / matched if matched else 0.0


# ── AUDN ────────────────────────────────────────────────────────


def audn_accuracy(
    cases: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute exact-match accuracy plus per-action breakdown.

    ``cases[i]`` is paired with ``decisions[i]``; both must have equal length.
    """
    if len(cases) != len(decisions):
        raise ValueError(
            f"length mismatch: {len(cases)} cases vs {len(decisions)} decisions"
        )

    total = len(cases)
    correct = 0
    per_action: dict[str, dict[str, int]] = {}
    confusions: list[dict[str, str]] = []

    for case, decision in zip(cases, decisions):
        expected = (case.get("expected_action") or "").lower()
        actual = (decision.get("action") or "").lower()

        bucket = per_action.setdefault(expected, {"total": 0, "correct": 0})
        bucket["total"] += 1
        if actual == expected:
            bucket["correct"] += 1
            correct += 1
        else:
            confusions.append(
                {
                    "case_id": case.get("case_id", "?"),
                    "expected": expected,
                    "actual": actual,
                }
            )

    return {
        "accuracy": correct / total if total else 0.0,
        "total": total,
        "correct": correct,
        "per_action": per_action,
        "confusions": confusions,
    }


# ── Page compile ────────────────────────────────────────────────

_FACT_ID_RE = re.compile(r"\[fact_id:([a-zA-Z0-9_\-]+)\]")
_BULLET_RE = re.compile(r"^\s*[-*]\s+", re.MULTILINE)


def extract_cited_fact_ids(content_md: str) -> list[str]:
    """Return all fact_ids cited by the page in the expected
    ``[fact_id:xxxxx]`` format.
    """
    return _FACT_ID_RE.findall(content_md or "")


def page_evaluation(
    case: dict[str, Any],
    content_md: str,
) -> dict[str, Any]:
    """Score a compiled page against its expected constraints.

    Returns a dict with per-check booleans + numeric scores so tests
    can assert on individual aspects or the aggregate.
    """
    expected = case.get("expected", {})
    content_lower = (content_md or "").lower()

    # Citation correctness — every expected fact_id must appear at least once.
    cited = set(extract_cited_fact_ids(content_md))
    must_cite = set(expected.get("must_cite_fact_ids", []))
    citation_recall = (
        len(cited & must_cite) / len(must_cite) if must_cite else 1.0
    )
    cited_real = cited.issubset(
        {f["fact_id"] for f in case.get("facts", [])}
    )

    # Keyword coverage — entity descriptors should appear.
    must_keywords = [k.lower() for k in expected.get("must_mention_keywords", [])]
    keyword_hits = [k for k in must_keywords if k in content_lower]
    keyword_coverage = (
        len(keyword_hits) / len(must_keywords) if must_keywords else 1.0
    )

    # Hallucination check — banned terms must NOT appear.
    forbidden = [k.lower() for k in expected.get("must_not_hallucinate_facts", [])]
    hallucinations = [k for k in forbidden if k in content_lower]

    # Bullet count.
    bullets = _BULLET_RE.findall(content_md or "")
    bullet_count = len(bullets)
    min_b = expected.get("min_bullets", 0)
    max_b = expected.get("max_bullets", 999)
    bullet_count_ok = min_b <= bullet_count <= max_b

    # Paragraph word bound — first paragraph until first bullet.
    first_para = (content_md or "").split("\n-")[0].split("\n*")[0]
    paragraph_words = len(first_para.split())
    paragraph_words_ok = paragraph_words <= expected.get("max_words_paragraph", 200)

    return {
        "citation_recall": citation_recall,
        "citation_ids_real": cited_real,
        "keyword_coverage": keyword_coverage,
        "hallucinations": hallucinations,
        "bullet_count": bullet_count,
        "bullet_count_ok": bullet_count_ok,
        "paragraph_words": paragraph_words,
        "paragraph_words_ok": paragraph_words_ok,
    }


# ── Aggregate / formatting ──────────────────────────────────────


def summarize(scores: list[float]) -> dict[str, float]:
    """Mean / min / max for a list of scalar scores."""
    if not scores:
        return {"mean": 0.0, "min": 0.0, "max": 0.0, "n": 0}
    return {
        "mean": sum(scores) / len(scores),
        "min": min(scores),
        "max": max(scores),
        "n": len(scores),
    }
