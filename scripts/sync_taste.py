#!/usr/bin/env python3
"""
Sync unsynced entries from feedback.json into taste.json iterate_when_chosen.

Usage:
    python3 scripts/sync_taste.py              # sync and show what changed
    python3 scripts/sync_taste.py --report     # show before/after lineage per piece
    python3 scripts/sync_taste.py --dry-run    # preview without writing

feedback.json entry schema:
    {
      "ts":     "2026-05-25",          # ISO date (or datetime)
      "piece":  "abc",                 # 3-char piece code (the "before")
      "text":   "user feedback text",  # raw directive text
      "synced": false,                 # set to true after sync
      "note":   "optional context"     # free-form note
    }

After sync, taste.json["iterate_when_chosen"]["abc"] is set/appended.
mutate.py consumes and removes the entry on next mutation of that piece.
feedback.json retains the entry permanently (synced=true) as history.
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FEEDBACK = REPO / "feedback.json"
TASTE = REPO / "taste.json"
LINEAGE = REPO / "lineage.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def lineage_index(pieces: list[dict]) -> dict[str, dict]:
    return {p["id"]: p for p in pieces}


def children_of(piece_id: str, pieces: list[dict]) -> list[dict]:
    return [p for p in pieces if p.get("parent_id") == piece_id]


def sync(dry_run: bool = False) -> int:
    feedback = load_json(FEEDBACK)
    taste = load_json(TASTE)

    unsynced = [e for e in feedback["entries"] if not e.get("synced")]
    if not unsynced:
        print("Nothing to sync.")
        return 0

    itc: dict = taste.setdefault("iterate_when_chosen", {})
    changed = 0

    for entry in unsynced:
        piece = entry["piece"]
        text = entry["text"].strip()
        ts = str(entry.get("ts", ""))[:10]

        if piece in itc:
            # Append new feedback with timestamp separator
            itc[piece] = itc[piece].rstrip() + f"\n\n[{ts}] {text}"
        else:
            itc[piece] = text

        if not dry_run:
            entry["synced"] = True
        changed += 1
        print(f"  {'[dry]' if dry_run else '+'} {piece}: {text[:80]}{'...' if len(text) > 80 else ''}")

    if not dry_run:
        taste["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        save_json(TASTE, taste)
        save_json(FEEDBACK, feedback)
        print(f"\nSynced {changed} entries → taste.json iterate_when_chosen.")
    else:
        print(f"\n[dry-run] would sync {changed} entries.")

    return changed


def report() -> None:
    feedback = load_json(FEEDBACK)
    lineage = load_json(LINEAGE)
    pieces = lineage.get("pieces", [])
    idx = lineage_index(pieces)

    print(f"{'PIECE':<6}  {'TS':<10}  {'BEFORE_TITLE':<40}  AFTER (children)")
    print("-" * 90)

    for entry in feedback["entries"]:
        piece = entry["piece"]
        ts = str(entry.get("ts", ""))[:10]
        before = idx.get(piece, {})
        before_title = (before.get("title") or piece)[:40]
        kids = children_of(piece, pieces)
        after_str = ", ".join(f"{k['id']} ({k.get('title', '')[:25]})" for k in kids) or "—"
        synced_mark = "✓" if entry.get("synced") else "○"
        print(f"{synced_mark} {piece:<5}  {ts:<10}  {before_title:<40}  {after_str}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report", action="store_true", help="Show before/after lineage for all feedback entries")
    parser.add_argument("--dry-run", action="store_true", help="Preview sync without writing")
    args = parser.parse_args()

    if args.report:
        report()
    else:
        sync(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
