#!/usr/bin/env python3
"""Rebuild lineage.json entries for pieces that exist on disk but are
missing from the lineage manifest.

Cause: under multi-cron contention with `pull --rebase -X theirs` strategy,
a tick that adds a NEW piece can have its lineage.json edit silently dropped
during rebase (theirs wins on conflict), even though the new piece's files
under pieces/<id>/ were preserved (no conflict, just an addition).

Symptom: pieces/<id>/index.html exists and is reachable on Vercel, but the
gallery index (which reads lineage.json) doesn't show it.

This script:
  1. Walks pieces/*/meta.json
  2. Compares to current lineage.json's piece-id set
  3. For each missing id, reconstructs a lineage entry + edge from meta.json
  4. Writes updated lineage.json (sorted by created_at to keep order stable)

Idempotent. Safe to run repeatedly.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LINEAGE = REPO / "lineage.json"
PIECES = REPO / "pieces"


def main():
    lineage = json.loads(LINEAGE.read_text())
    known_ids = {p["id"] for p in lineage["pieces"]}

    on_disk = []
    for piece_dir in sorted(PIECES.iterdir()):
        if not piece_dir.is_dir():
            continue
        meta_path = piece_dir / "meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text())
        except Exception as e:
            print(f"WARN: skip {piece_dir.name} — bad meta.json: {e}", file=sys.stderr)
            continue
        on_disk.append((piece_dir.name, meta))

    missing = [(pid, meta) for pid, meta in on_disk if pid not in known_ids]
    print(f"on-disk: {len(on_disk)}  in-lineage: {len(known_ids)}  missing: {len(missing)}")
    if not missing:
        return 0

    edges_known = {(e.get("from"), e.get("to")) for e in lineage.get("edges", [])}

    added = 0
    for pid, meta in missing:
        entry = {
            "id": pid,
            "title": meta.get("title", f"piece {pid}"),
            "direction": meta.get("direction", ""),
            "input": meta.get("input", "unknown"),
            "particle_count": meta.get("particle_count"),
            "parent_id": meta.get("parent_id"),
            "generation": meta.get("generation", 0),
            "created_at": meta.get("created_at", datetime.now(timezone.utc).isoformat()),
        }
        lineage["pieces"].append(entry)

        if meta.get("parent_id"):
            edge_key = (meta["parent_id"], pid)
            if edge_key not in edges_known:
                directive = (meta.get("mutation_directive_id")
                             or meta.get("mutation_directive")
                             or "unknown")
                lineage.setdefault("edges", []).append({
                    "from": meta["parent_id"],
                    "to": pid,
                    "directive": directive,
                })
                edges_known.add(edge_key)
        added += 1
        parent_str = meta.get('parent_id') or "root"
        print(f"  + {pid:6s}  parent={parent_str:>5}  {meta.get('title','')[:60]}")

    # Re-sort pieces by created_at to keep the manifest stable across reconciles
    lineage["pieces"].sort(key=lambda p: p.get("created_at", ""))
    lineage["updated_at"] = datetime.now(timezone.utc).isoformat()
    LINEAGE.write_text(json.dumps(lineage, indent=2, ensure_ascii=False))
    print(f"\nadded {added} entries; lineage now has {len(lineage['pieces'])} pieces")
    return 0


if __name__ == "__main__":
    sys.exit(main())
