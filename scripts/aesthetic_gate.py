#!/usr/bin/env python3
"""Aesthetic gate — runs between the render-content gate and the git commit
in scripts/mutate.py. Reads bug.md (the user's catalogue of aesthetic
anti-patterns), shows the rendered piece's HTML excerpt to Claude, and
asks whether the piece exhibits any flagged pattern.

Failing pieces are REJECTED — they don't ship to Vercel. But before
deletion, any reusable IDEAS in the piece's code/concept are extracted
to scripts/idea_extracts.jsonl so the genius-bits survive even when the
piece doesn't.

Cost: 1 Claude call per mutation (subscription primary, Bedrock fallback).
~$0.07/call on Bedrock; $0 on subscription.
"""
import json, sys, datetime, re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUG_MD = REPO / "bug.md"
IDEA_LOG = REPO / "scripts" / "idea_extracts.jsonl"


def _read_bugs() -> str:
    if not BUG_MD.exists():
        return ""
    return BUG_MD.read_text()[:6000]


def check(html: str, piece_id: str, parent: dict, directive: str) -> tuple[bool, list[str], list[str]]:
    """Returns (pass, anti_patterns_hit, extractable_ideas).

    On any failure (Claude error, JSON parse error, bug.md missing): default
    to PASS — the gate is non-fatal infrastructure, not a stricter render gate.
    """
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import lib_claude
    except ImportError:
        return True, [], []

    bugs = _read_bugs()
    if not bugs:
        return True, [], []

    # truncate HTML aggressively — the critic reads structure, not every line
    html_excerpt = html[:6000] + ("\n... [truncated] ..." if len(html) > 6000 else "")

    system = (
        "You are an aesthetic gate critic for an evolutionary particle-art gallery. "
        "Read the user's catalogue of documented aesthetic anti-patterns (bug.md) below. "
        "Then read a freshly-mutated piece's metadata + HTML excerpt. "
        "Determine: does this piece exhibit ANY of the documented anti-patterns?\n\n"
        "Be STRICT but FAIR — a piece that merely *resembles* an anti-pattern from a distance "
        "passes; a piece that *actually does the failure mode described* fails. "
        "If the piece fails, also extract any reusable creative IDEAS hidden inside it — "
        "the directive applied, novel technical approach, surprising material/concept choice, "
        "etc. — so the genius bits survive the cull.\n\n"
        "--- bug.md (user's anti-pattern catalogue) ---\n"
        f"{bugs}\n"
        "--- end bug.md ---\n\n"
        "Return STRICT JSON only:\n"
        '{"pass": true|false,\n'
        ' "anti_patterns_hit": ["short ref to bug.md entry, e.g. dots-on-intestine"],\n'
        ' "reasoning": "one sentence explaining the call",\n'
        ' "extractable_ideas": ["concrete ideas worth keeping if the piece fails — e.g. \'use Hilbert curve to anchor particle drift\'"]}\n'
    )
    user = (
        f"# Piece: {piece_id}\n"
        f"## Parent: {parent.get('id','?')} — {parent.get('title','?')}\n"
        f"## Direction: {parent.get('direction','?')}\n"
        f"## Mutation directive applied:\n{directive[:1500]}\n\n"
        f"## Generated HTML (excerpt):\n```html\n{html_excerpt}\n```"
    )

    try:
        text, _ = lib_claude.call(system, user)
    except Exception as e:
        print(f"  aesthetic gate: SKIPPED (provider error: {e})")
        return True, [], []

    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return True, [], []
    try:
        parsed = json.loads(m.group(0))
    except Exception:
        return True, [], []

    passed = bool(parsed.get("pass", True))
    hits = parsed.get("anti_patterns_hit") or []
    ideas = parsed.get("extractable_ideas") or []
    reasoning = parsed.get("reasoning", "")

    if not passed:
        print(f"  aesthetic gate: FAIL — {reasoning}")
        if hits:
            print(f"    anti-patterns: {', '.join(hits)}")
        if ideas:
            entry = {
                "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "piece": piece_id,
                "parent": parent.get("id"),
                "directive": directive[:500],
                "anti_patterns_hit": hits,
                "reasoning": reasoning,
                "extractable_ideas": ideas,
            }
            IDEA_LOG.parent.mkdir(parents=True, exist_ok=True)
            with IDEA_LOG.open("a") as f:
                f.write(json.dumps(entry) + "\n")
            print(f"    extracted {len(ideas)} idea(s) → scripts/idea_extracts.jsonl")
    else:
        print(f"  aesthetic gate: PASS")

    return passed, hits, ideas


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: aesthetic_gate.py <piece_id>", file=sys.stderr)
        sys.exit(2)
    pid = sys.argv[1]
    html_path = REPO / "pieces" / pid / "index.html"
    meta_path = REPO / "pieces" / pid / "meta.json"
    if not html_path.exists():
        print(f"no piece at {html_path}", file=sys.stderr); sys.exit(2)
    html = html_path.read_text()
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    parent = {"id": meta.get("parent_id"), "title": "", "direction": meta.get("direction","")}
    directive = meta.get("mutation_directive", "")
    ok, hits, ideas = check(html, pid, parent, directive)
    sys.exit(0 if ok else 1)
