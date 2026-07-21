#!/usr/bin/env python3
"""Reconcile browser curation into durable, append-only project state."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PREFERENCES = ROOT / "scripts" / "preferences.json"
DELETED = ROOT / "scripts" / "deleted_pieces.json"
LINEAGE = ROOT / "lineage.json"
CHROME_LEVELDB = Path.home() / "Library/Application Support/Google/Chrome/Default/Local Storage/leveldb"


def load(path: Path, fallback: dict) -> dict:
    return json.loads(path.read_text()) if path.exists() else fallback


def atomic_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def browser_deletions(origin: str) -> list[str]:
    """Read the newest valid dismissed-ID array for an origin from Chrome LevelDB strings."""
    if not CHROME_LEVELDB.exists():
        return []
    candidates: list[tuple[float, int, list[str]]] = []
    files = sorted(
        (p for p in CHROME_LEVELDB.iterdir() if p.suffix in {".log", ".ldb"}),
        key=lambda p: p.stat().st_mtime,
    )
    for path in files:
        result = subprocess.run(["strings", str(path)], text=True, capture_output=True, check=False)
        active_origin = False
        awaiting_value = 0
        for position, line in enumerate(result.stdout.splitlines()):
            if "http://" in line or "https://" in line:
                active_origin = origin in line
            if "particle_art_dismissed" in line and active_origin:
                awaiting_value = 12
                continue
            if awaiting_value:
                awaiting_value -= 1
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, list) and all(isinstance(x, str) for x in value):
                    candidates.append((path.stat().st_mtime, position, value))
                    awaiting_value = 0
    return max(candidates, default=(0, 0, []), key=lambda x: (x[0], x[1]))[2]


def apply_deletions(ids: set[str]) -> None:
    known = {p["id"] for p in load(LINEAGE, {"pieces": []}).get("pieces", [])}
    unknown = sorted(ids - known)
    if unknown:
        raise SystemExit(f"Refusing unknown piece IDs: {', '.join(unknown)}")

    prefs = load(PREFERENCES, {"marks": {}})
    marks = prefs.setdefault("marks", {})
    for piece_id in ids:
        mark = marks.setdefault(piece_id, {})
        mark["drop"] = True
        mark.pop("favorite", None)
        mark.pop("star", None)
    prefs["updated_at"] = date.today().isoformat()

    deleted = load(DELETED, {"version": 1, "ids": []})
    deleted["ids"] = sorted(set(deleted.get("ids", [])) | ids)
    deleted["updated_at"] = date.today().isoformat()
    atomic_json(PREFERENCES, prefs)
    atomic_json(DELETED, deleted)


def verify() -> list[str]:
    prefs = load(PREFERENCES, {"marks": {}})
    tombstones = set(load(DELETED, {"ids": []}).get("ids", []))
    errors = []
    for piece_id in sorted(tombstones):
        mark = prefs.get("marks", {}).get(piece_id, {})
        if not mark.get("drop"):
            errors.append(f"{piece_id}: tombstoned but not drop-marked")
        if mark.get("favorite") or mark.get("star"):
            errors.append(f"{piece_id}: tombstoned but still favorite/starred")
    for page in ("index.html", "favorites.html", "best.html", "all.html"):
        if "deleted_pieces.json" not in (ROOT / page).read_text():
            errors.append(f"{page}: does not consume deleted_pieces.json")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="localhost:8765", help="Browser origin substring")
    parser.add_argument("--ids", help="Comma/space-separated explicit piece IDs")
    parser.add_argument("--apply", action="store_true", help="Persist new IDs into preferences and tombstones")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable report")
    args = parser.parse_args()

    explicit = set(re.findall(r"[a-z0-9]{3}", args.ids.lower())) if args.ids else set()
    observed = explicit or set(browser_deletions(args.origin))
    existing = set(load(DELETED, {"ids": []}).get("ids", []))
    new_ids = sorted(observed - existing)
    if args.apply and new_ids:
        apply_deletions(set(new_ids))
    errors = verify()
    report = {
        "origin": args.origin,
        "observed": len(observed),
        "previously_tombstoned": len(existing),
        "new_ids": new_ids,
        "applied": len(new_ids) if args.apply else 0,
        "verification_errors": errors,
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Observed {len(observed)} removals; {len(new_ids)} new.")
        if new_ids:
            print("New IDs: " + ", ".join(new_ids))
        print("Applied." if args.apply and new_ids else "No state changed.")
        print("Verification: " + ("PASS" if not errors else "FAIL\n- " + "\n- ".join(errors)))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
