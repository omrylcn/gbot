"""LOCOMO-mini benchmark suite (Faz 22E Step 3).

Opt-in via ``pytest -m benchmark``. Runs an isolated store + 30 facts +
25 queries to produce ``recall@K``, ``MRR``, ``latency``, ``tokens``
stats. Used for version-to-version regression detection and
``entity_pages.enabled=true/false`` A/B comparison.
"""
