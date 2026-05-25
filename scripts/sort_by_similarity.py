#!/usr/bin/env python3
"""
Sort lineage.json pieces by visual similarity.

Strategy
--------
1. Load 32×32 grayscale feature vectors from thumbs/ for pieces that have them.
2. Group pieces by direction.
3. Order the direction groups by centroid-NN traversal (visually similar
   direction families end up adjacent).
4. Within each direction group, order pieces by greedy NN traversal through
   their feature vectors; pieces without thumbs fall back to created_at order
   and are appended after the visual cluster.
5. Write updated lineage.json (pieces array reordered; all other fields intact).

Idempotent — safe to re-run after new thumbs are rendered.
"""
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

REPO    = Path(__file__).resolve().parent.parent
LINEAGE = REPO / "lineage.json"
THUMBS  = REPO / "thumbs"
SIZE    = 32   # px per side for feature extraction


# ── feature loading ───────────────────────────────────────────────────────────

def load_features(thumb_dir: Path) -> dict[str, np.ndarray]:
    """Return {code: unit-norm 1024-d vector} for every usable thumbnail."""
    feats: dict[str, np.ndarray] = {}
    for png in thumb_dir.glob("*.png"):
        code = png.stem
        try:
            img = Image.open(png).convert("L").resize((SIZE, SIZE), Image.LANCZOS)
            v = np.array(img, dtype=np.float32).flatten()
            norm = np.linalg.norm(v)
            feats[code] = v / (norm + 1e-8)
        except Exception as e:
            print(f"  WARN: skip {png.name}: {e}", file=sys.stderr)
    return feats


# ── ordering ──────────────────────────────────────────────────────────────────

def nn_order(codes: list[str],
             feats: dict[str, np.ndarray],
             dates: dict[str, str]) -> list[str]:
    """
    Greedy nearest-neighbour path through `codes`.
    Pieces with image features are ordered by visual similarity;
    pieces without thumbs are appended sorted by created_at.
    """
    have = [c for c in codes if c in feats]
    nope = sorted([c for c in codes if c not in feats],
                  key=lambda c: dates.get(c) or "")
    if not have:
        return nope

    # Build matrix for fast dot products
    mat = np.stack([feats[c] for c in have])   # (n, 1024)
    idx_map = {c: i for i, c in enumerate(have)}

    remaining = set(range(len(have)))
    # Start from the earliest-dated piece that has a thumb
    first_code = min(have, key=lambda c: dates.get(c) or "z")
    path = [idx_map[first_code]]
    remaining.remove(path[0])

    while remaining:
        last_v = mat[path[-1]]
        sims = mat[list(remaining)] @ last_v   # cosine (vectors are unit-normed)
        best_local = int(np.argmax(sims))
        best_global = list(remaining)[best_local]
        path.append(best_global)
        remaining.remove(best_global)

    return [have[i] for i in path] + nope


def centroid(codes: list[str], feats: dict[str, np.ndarray]) -> np.ndarray | None:
    vecs = [feats[c] for c in codes if c in feats]
    if not vecs:
        return None
    c = np.mean(vecs, axis=0)
    return c / (np.linalg.norm(c) + 1e-8)


def order_groups(group_names: list[str],
                 centroids: dict[str, np.ndarray],
                 group_sizes: dict[str, int]) -> list[str]:
    """
    NN traversal of direction groups by centroid similarity.
    Groups without a centroid (no thumbs) are appended alphabetically at the end.
    """
    have = [d for d in group_names if d in centroids]
    nope = sorted([d for d in group_names if d not in centroids])
    if not have:
        return nope

    mat = np.stack([centroids[d] for d in have])
    idx_map = {d: i for i, d in enumerate(have)}
    remaining = set(range(len(have)))

    # Start from largest direction group (highest visual mass)
    first = max(have, key=lambda d: group_sizes[d])
    path = [idx_map[first]]
    remaining.remove(path[0])

    while remaining:
        last_v = mat[path[-1]]
        sims = mat[list(remaining)] @ last_v
        best_local = int(np.argmax(sims))
        best_global = list(remaining)[best_local]
        path.append(best_global)
        remaining.remove(best_global)

    return [have[i] for i in path] + nope


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    lineage = json.loads(LINEAGE.read_text())
    pieces  = lineage["pieces"]
    print(f"pieces: {len(pieces)}")

    print(f"loading features from {THUMBS} …")
    feats = load_features(THUMBS)
    # only keep thumbs whose code is actually in lineage
    lin_ids = {p["id"] for p in pieces}
    feats   = {c: v for c, v in feats.items() if c in lin_ids}
    print(f"  {len(feats)} usable thumbs ({len(feats)*100//len(pieces)}% coverage)")

    dates     = {p["id"]: p.get("created_at") or "" for p in pieces}
    piece_map = {p["id"]: p for p in pieces}

    # Group by direction
    dir_groups: dict[str, list[str]] = defaultdict(list)
    for p in pieces:
        dir_groups[p.get("direction") or ""].append(p["id"])

    # Per-direction centroids
    centroids = {d: c for d, codes in dir_groups.items()
                 if (c := centroid(codes, feats)) is not None}
    print(f"  {len(centroids)}/{len(dir_groups)} directions have thumb coverage")

    # Order directions
    dir_order = order_groups(list(dir_groups.keys()), centroids,
                             {d: len(c) for d, c in dir_groups.items()})

    # Build sorted piece list
    sorted_pieces: list[dict] = []
    for d in dir_order:
        codes   = dir_groups[d]
        ordered = nn_order(codes, feats, dates)
        for code in ordered:
            sorted_pieces.append(piece_map[code])
        label = d[:50] if d else "(no direction)"
        thumb_n = sum(1 for c in codes if c in feats)
        print(f"  {len(codes):4d} pieces  {thumb_n:3d} thumbs  {label}")

    assert len(sorted_pieces) == len(pieces), \
        f"piece count mismatch: {len(sorted_pieces)} vs {len(pieces)}"

    lineage["pieces"]     = sorted_pieces
    lineage["updated_at"] = datetime.now(timezone.utc).isoformat()
    LINEAGE.write_text(json.dumps(lineage, indent=2, ensure_ascii=False))
    print(f"\nlineage.json rewritten — {len(sorted_pieces)} pieces across "
          f"{len(dir_order)} direction groups")
    return 0


if __name__ == "__main__":
    sys.exit(main())
