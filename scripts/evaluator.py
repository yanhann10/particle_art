#!/usr/bin/env python3
"""Taste-aware LLM evaluator — scores recent pieces, queues improvement directives.

Usage:
    python3 scripts/evaluator.py              # evaluate last 15 pieces
    python3 scripts/evaluator.py --limit 5   # evaluate fewer pieces
    python3 scripts/evaluator.py --dry-run   # print directives without writing
"""
import argparse, json, sys
from datetime import datetime, timezone
from pathlib import Path

REPO    = Path(__file__).resolve().parent.parent
PIECES  = REPO / "pieces"
TASTE   = REPO / "taste.json"
PREFS   = REPO / "scripts" / "preferences.json"
MUT_LOG = REPO / "scripts" / "mutation_log.jsonl"
PENDING = REPO / "scripts" / "pending_directives.jsonl"

sys.path.insert(0, str(REPO / "scripts"))
from lib_claude import call_subscription, call_bedrock, ProviderError

SYSTEM = (
    "You are an art evaluator for a generative particle art system. "
    "Given the user's taste profile and a piece's source code, decide: "
    "does this piece need improvement? If yes, return a single mutation directive "
    "(imperative, max 2 sentences) that would move it toward the taste profile. "
    "If it already aligns well, return exactly: PASS"
)


def _load_json(p):
    try: return json.loads(p.read_text()) if p.exists() else {}
    except Exception: return {}


def _taste_summary(taste, prefs):
    L, D = taste.get("likes", {}), taste.get("dislikes", {})
    marks = (prefs.get("marks", {}) if isinstance(prefs, dict) else {})
    favs = [f"  {k}: {v['note']}" for k, v in marks.items()
            if v.get("favorite") and v.get("note")][:6]
    parts = [
        "LIKES (directions): " + "; ".join(L.get("directions", [])[:8]),
        "LIKES (techniques): " + "; ".join(L.get("techniques", [])[:5]),
        "DISLIKES: " + "; ".join(D.get("directions", [])[:6]),
    ]
    if favs:
        parts.append("FAVORITES:\n" + "\n".join(favs))
    return "\n".join(parts)


def _recent_pieces(limit):
    if not MUT_LOG.exists(): return []
    entries = []
    for ln in MUT_LOG.read_text().splitlines():
        try:
            e = json.loads(ln)
            if e.get("id") and e.get("ts"): entries.append(e)
        except Exception: pass
    entries.sort(key=lambda x: x["ts"], reverse=True)
    seen, result = set(), []
    for e in entries:
        pid = e["id"]
        if pid not in seen:
            seen.add(pid); result.append(pid)
        if len(result) >= limit: break
    return result


def _queued_ids():
    if not PENDING.exists(): return set()
    ids = set()
    for ln in PENDING.read_text().splitlines():
        try:
            e = json.loads(ln)
            if e.get("source") == "evaluator" and e.get("parent_id"):
                ids.add(e["parent_id"])
        except Exception: pass
    return ids


def _call(system, user):
    r = call_subscription(system, user)
    if r and r.strip(): return r.strip()
    return call_bedrock(system, user).strip()


def main():
    ap = argparse.ArgumentParser(description="Taste-aware LLM evaluator")
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    taste   = _load_json(TASTE)
    prefs   = _load_json(PREFS)
    summary = _taste_summary(taste, prefs)

    piece_ids  = _recent_pieces(args.limit)
    queued_ids = _queued_ids()
    now_ts     = datetime.now(timezone.utc).isoformat()

    evaluated = queued = 0
    for pid in piece_ids:
        html_path = PIECES / pid / "index.html"
        if not html_path.exists(): continue
        if pid in queued_ids: continue

        html = html_path.read_text(errors="replace")[:3000]
        user_msg = (
            f"TASTE PROFILE:\n{summary}\n\n"
            f"PIECE ID: {pid}\nSOURCE (first 3000 chars):\n```html\n{html}\n```"
        )

        try:
            response = _call(SYSTEM, user_msg)
        except ProviderError as e:
            print(f"  [{pid}] provider error: {e}", file=sys.stderr)
            evaluated += 1
            continue

        evaluated += 1
        if response.upper().startswith("PASS"):
            print(f"  [{pid}] PASS")
            continue

        directive = response.strip()
        print(f"  [{pid}] DIRECTIVE: {directive}")

        if not args.dry_run:
            entry = {
                "source": "evaluator", "parent_id": pid,
                "priority_directive": directive, "queued_at": now_ts,
            }
            with PENDING.open("a") as f:
                f.write(json.dumps(entry) + "\n")
        queued += 1

    suffix = " (dry-run)" if args.dry_run else ""
    print(f"\nevaluator: {evaluated} pieces evaluated, {queued} directives queued{suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
