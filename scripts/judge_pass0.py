#!/usr/bin/env python3
"""Pass 0 of the gallery cleanse (issue #13): apply the CURRENT judge's
deterministic layers to every main-page piece and demote hard fails to
staging.json.

"Current judge" = the two non-LLM gates every new piece must pass today:
  1. render-gate pixel stats (validate_render.py thresholds), computed
     here on the EXISTING CI thumbnail — no re-render needed
  2. precheck.py hard bans (autoRotate, camera shake)

The HTML-text critic (critic.py) is deliberately NOT run en masse: it is
the blind component issue #13 replaces, and 1,100 calls of it would cost
~$35 for judgments that never saw a pixel.

Usage:
    python3 scripts/judge_pass0.py            # dry run, print verdicts
    python3 scripts/judge_pass0.py --apply    # write staging.json
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import precheck
from validate_render import (
    MIN_NONBACKGROUND_FRACTION,
    MIN_GRAYSCALE_STDDEV,
    MIN_LUMA_DYNAMIC_RANGE,
    MIN_SHARPNESS_VARIANCE,
)

STAGING = REPO / "staging.json"


def thumb_stats(png: Path) -> dict:
    arr = np.array(Image.open(png).convert("RGB"))
    bg = np.median(arr.reshape(-1, 3), axis=0)
    dist = np.abs(arr.astype(np.int32) - bg.astype(np.int32)).sum(axis=2)
    non_bg = (dist > 12).sum() / dist.size
    gray = arr.astype(np.float32) @ np.array([0.299, 0.587, 0.114])
    p1, p99 = np.percentile(gray, [1, 99])
    lap = (gray[2:, 1:-1] - 2 * gray[1:-1, 1:-1] + gray[:-2, 1:-1]
           + gray[1:-1, 2:] - 2 * gray[1:-1, 1:-1] + gray[1:-1, :-2])
    return {
        "non_bg": float(non_bg),
        "stddev": float(gray.std()),
        "range": float(p99 - p1),
        "sharpness": float(lap.var()),
    }


def gate_verdict(s: dict) -> str | None:
    """Same thresholds + order as validate_render.validate()."""
    if s["non_bg"] < MIN_NONBACKGROUND_FRACTION:
        return f"empty render — only {s['non_bg']*100:.2f}% of pixels differ from background"
    if s["stddev"] < MIN_GRAYSCALE_STDDEV:
        return f"low contrast — grayscale stddev {s['stddev']:.1f} (gate: ≥{MIN_GRAYSCALE_STDDEV:.0f})"
    if s["range"] < MIN_LUMA_DYNAMIC_RANGE:
        return f"narrow dynamic range — luma p99–p1 {s['range']:.1f} (gate: ≥{MIN_LUMA_DYNAMIC_RANGE:.0f})"
    # NOTE: the sharpness gate (≥150) is deliberately NOT applied in pass 0.
    # It was recalibrated 2026-05-24 (30 → 150) and retroactively fails
    # user-validated seeds (ede=80.6, 61r=130.8, ank=109.1) that passed the
    # gate in force when they shipped. The video judge (pass 1) re-evaluates
    # everything; pass 0 demotes only unambiguous fails.
    return None


def load_staging() -> dict:
    if STAGING.exists():
        return json.loads(STAGING.read_text())
    return {"_help": "Pieces demoted from the main gallery by the aesthetic judge "
                     "(issue #13). index.html hides these ids; staging.html lists them "
                     "with the judge's verdict. Delete an entry to restore a piece.",
            "updated_at": "", "staged": {}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write staging.json")
    args = ap.parse_args()

    lineage = json.loads((REPO / "lineage.json").read_text())
    prefs = json.loads((REPO / "scripts" / "preferences.json").read_text())
    marks = prefs.get("marks", {})
    dropped = {k for k, v in marks.items() if v.get("drop")}
    favorites = {k for k, v in marks.items() if v.get("favorite")}

    staging = load_staging()
    demoted, missing = [], []
    for piece in lineage["pieces"]:
        pid = piece["id"]
        if pid in dropped or pid in favorites:   # favorites are user-protected
            continue
        html_path = REPO / "pieces" / pid / "index.html"
        thumb = REPO / "thumbs" / f"{pid}.png"
        if not html_path.exists() or not thumb.exists():
            missing.append(pid)
            continue

        reasons = []
        v = gate_verdict(thumb_stats(thumb))
        if v:
            reasons.append(v)
        pc = precheck.run(html_path.read_text())
        for viol in pc["violations"]:
            reasons.append(f"banned technique: {viol['description']}")

        if reasons:
            demoted.append((pid, "; ".join(reasons)))

    print(f"{len(demoted)} of {len(lineage['pieces'])} main-page pieces fail the current judge")
    for pid, why in demoted:
        print(f"  ✗ {pid}: {why}")
    if missing:
        print(f"  ({len(missing)} skipped — missing html/thumb: {' '.join(missing)})")

    if args.apply:
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        for pid, why in demoted:
            staging["staged"][pid] = {
                "run": "pass0-static-gate",
                "judge": "render-gate+precheck (deterministic)",
                "score": None,
                "verdict": why,
                "resembles": None,
                "staged_at": now,
            }
        staging["updated_at"] = now
        STAGING.write_text(json.dumps(staging, indent=2) + "\n")
        print(f"\nwrote {len(demoted)} entries → staging.json")


if __name__ == "__main__":
    main()
