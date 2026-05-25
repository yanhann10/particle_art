#!/usr/bin/env python3
"""One-off backfill: add style_key to every piece in lineage.json that lacks it.

Reads pieces/<id>/meta.json for tags; if meta is missing, derives from the
lineage edge directive field. Safe to run multiple times (idempotent).
"""
import json
import sys
from pathlib import Path

REPO    = Path(__file__).resolve().parent.parent
LINEAGE = REPO / "lineage.json"
PIECES  = REPO / "pieces"


def _style_key_from_tags(tags: list) -> str:
    for t in tags:
        if t.startswith("channel:"):
            return t
    for t in tags:
        if t.startswith("mode:"):
            return t
    directives = [t for t in tags if ":" not in t and t not in (
        "improv", "cross-pollinated", "volumetric-light", "multi-state", "object-cloud",
    )]
    return directives[-1] if directives else "directive"


def _style_key_from_edge_directive(directive: str) -> str:
    """Fallback when meta.json is missing — derive from edge directive string."""
    if not directive:
        return "directive"
    if directive.startswith("channel_"):
        return directive.replace("channel_", "channel:")
    if directive.startswith("improv:"):
        return "mode:chain"  # conservative fallback
    return directive.replace("_", "-")


def main():
    lineage = json.loads(LINEAGE.read_text())

    # Build directive lookup from edges: piece_id → directive used to create it
    edge_directives = {e["to"]: e.get("directive", "") for e in lineage.get("edges", [])}

    updated = 0
    for piece in lineage["pieces"]:
        if "style_key" in piece:
            continue
        pid = piece["id"]
        meta_path = PIECES / pid / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                tags = meta.get("tags") or []
                piece["style_key"] = _style_key_from_tags(tags)
            except Exception:
                piece["style_key"] = _style_key_from_edge_directive(edge_directives.get(pid, ""))
        else:
            piece["style_key"] = _style_key_from_edge_directive(edge_directives.get(pid, ""))
        updated += 1

    LINEAGE.write_text(json.dumps(lineage, indent=2, ensure_ascii=False))
    print(f"backfill_style_keys: updated {updated} pieces in lineage.json")


if __name__ == "__main__":
    main()
