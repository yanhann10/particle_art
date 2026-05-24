#!/usr/bin/env python3
"""Drop an original piece and re-parent a new piece to the original's parent.

Called by feedback_api.py /replace route after the user clicks "Replace".

Usage:
    python scripts/drop_and_reparent.py --drop <orig_id> --new-id <new_id>
"""
import argparse
import json
import subprocess
from pathlib import Path

REPO    = Path(__file__).resolve().parent.parent
LINEAGE = REPO / "lineage.json"


def run_git(*args):
    return subprocess.run(["git", *args], cwd=str(REPO), check=True,
                          capture_output=True, text=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drop",   required=True, help="original piece id to drop")
    ap.add_argument("--new-id", required=True, help="new piece id to re-parent")
    args = ap.parse_args()

    lineage = json.loads(LINEAGE.read_text())
    pieces  = lineage["pieces"]

    # Find original's parent
    orig = next((p for p in pieces if p["id"] == args.drop), None)
    if not orig:
        print(f"drop_id {args.drop} not found in lineage")
        return 1

    # Find new piece
    new_piece = next((p for p in pieces if p["id"] == args.new_id), None)
    if not new_piece:
        print(f"new_id {args.new_id} not found in lineage")
        return 1

    orig_parent = orig.get("parent_id")

    # Mark original as dropped
    orig["dropped"] = True

    # Re-parent new piece to original's parent (skip the dropped intermediary)
    if orig_parent:
        new_piece["parent_id"] = orig_parent

    LINEAGE.write_text(json.dumps(lineage, indent=2))
    print(f"Dropped {args.drop}, re-parented {args.new_id} → {orig_parent or 'root'}")

    run_git("add", "lineage.json")
    run_git("commit", "-m", f"replace: drop {args.drop}, reparent {args.new_id} to {orig_parent or 'root'}")
    run_git("push")
    print("Pushed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
