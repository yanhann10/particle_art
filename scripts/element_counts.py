"""Inverse-frequency bias for element samplers (directives / words / artists).

All counts derive from existing logs — single source of truth, no extra
state file to drift:
    mutation_log.jsonl  → directive_id
    improv_log.jsonl    → word (per mode), directive_id
    theatrical_log.jsonl → word (if present)

Use bias_weights(...) to nudge any weighted sampler toward under-used
elements without ever hard-excluding one. β=0 disables the bias;
β=1.0 = moderate variety push; β=2.0 = aggressive.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Callable, Iterable, Sequence

REPO = Path(__file__).resolve().parent.parent
MUTATION_LOG   = REPO / "scripts" / "mutation_log.jsonl"
IMPROV_LOG     = REPO / "scripts" / "improv_log.jsonl"
THEATRICAL_LOG = REPO / "scripts" / "theatrical_log.jsonl"


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for ln in path.read_text().splitlines():
        try:
            out.append(json.loads(ln))
        except Exception:
            # skip malformed line — never fail a tick on log corruption
            pass
    return out


def directive_counts() -> Counter:
    """How many times each `directive_id` has fired across all tick logs."""
    c: Counter = Counter()
    for row in _read_jsonl(MUTATION_LOG):
        d = row.get("directive_id")
        if d:
            c[d] += 1
    for row in _read_jsonl(IMPROV_LOG):
        d = row.get("directive_id")
        if d:
            c[d] += 1
    return c


def word_counts(mode: str | None = None) -> Counter:
    """Per-`word` count across improv + theatrical logs.

    If `mode` is given, only improv rows with that mode contribute (used to
    count how often each artist-personality has been channeled, etc.).
    """
    c: Counter = Counter()
    for row in _read_jsonl(IMPROV_LOG):
        if mode is not None and row.get("mode") != mode:
            continue
        w = row.get("word")
        if w:
            c[w] += 1
    if mode is None:
        # theatrical only contributes to the global word pool, not artist mode
        for row in _read_jsonl(THEATRICAL_LOG):
            w = row.get("word")
            if w:
                c[w] += 1
    return c


def bias_weights(
    items: Sequence,
    base_weights: Sequence[float],
    counts: Counter,
    key: Callable,
    beta: float = 1.0,
) -> list[float]:
    """Return base / (1 + count(key(item)))**beta — preserves zeros, never excludes.

    The (1 + n) shape means an unused element keeps its base weight while a
    once-used element drops to 1/2^β, twice-used to 1/3^β, and so on.
    """
    out: list[float] = []
    for it, w in zip(items, base_weights):
        n = counts.get(key(it), 0)
        out.append(float(w) / (1.0 + n) ** beta)
    return out
