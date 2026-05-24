#!/usr/bin/env python3
"""Pinterest inspiration pipeline — orchestrates scraper + art critic.

Step 1: Scrape pins from the Pinterest board → pinterest_pins.json
Step 2: VLM art-critic analysis of each pin → pinterest_critique.jsonl
Step 3: Print top-scoring directives (highest inspiration_score)
(Step 4 — maker-agent communication — wired separately)

Usage:
    # Full run
    python scripts/run_pinterest_pipeline.py

    # Skip re-scraping (use cached pins)
    python scripts/run_pinterest_pipeline.py --skip-scrape

    # Dry-run critic (no Bedrock calls)
    python scripts/run_pinterest_pipeline.py --dry-run

    # Tune limit
    python scripts/run_pinterest_pipeline.py --limit 20
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO         = Path(__file__).resolve().parent.parent
DEFAULT_BOARD = "https://www.pinterest.com/hannahyan/particle-art/"
CRITIQUE_LOG  = REPO / "scripts" / "pinterest_critique.jsonl"

# Two-file dedup split:
#   pinterest_pins.json  — raw scrape cache written by pinterest_scraper.py;
#                          read by pinterest_critic_agent.py each pipeline run.
#   seen_pins.json       — persistent dedup registry used by the OAuth-based
#                          pinterest_agent.py to skip already-processed pins.
#                          This pipeline ALSO updates it so that switching
#                          between the two flows doesn't re-process pins.
PINS_CACHE = REPO / "scripts" / "pinterest_pins.json"
SEEN_PINS  = REPO / "scripts" / "seen_pins.json"


def update_seen_pins() -> None:
    """Merge IDs from pinterest_pins.json into seen_pins.json.

    Called after a successful critic run so that re-running the pipeline
    (or switching to pinterest_agent.py's OAuth flow) does not re-process
    the same pins.
    """
    if not PINS_CACHE.exists():
        return
    try:
        scraped_ids = {p["id"] for p in json.loads(PINS_CACHE.read_text()) if p.get("id")}
    except Exception as e:
        print(f"  warn: could not read {PINS_CACHE.name}: {e}")
        return

    existing: list = []
    if SEEN_PINS.exists():
        try:
            existing = json.loads(SEEN_PINS.read_text())
        except Exception:
            pass

    merged = sorted(set(existing) | scraped_ids)
    SEEN_PINS.write_text(json.dumps(merged, indent=2))
    new_count = len(merged) - len(existing)
    print(f"  seen_pins.json updated: {len(merged)} total ({new_count} new)")


def run_step(label: str, cmd: list[str]) -> int:
    print(f"\n{'─'*60}")
    print(f"  {label}")
    print(f"  $ {' '.join(cmd)}")
    print(f"{'─'*60}")
    result = subprocess.run(cmd, cwd=str(REPO))
    return result.returncode


def top_directives(n: int = 5) -> None:
    if not CRITIQUE_LOG.exists():
        return
    lines = [l for l in CRITIQUE_LOG.read_text().strip().splitlines() if l.strip()]
    entries = []
    for l in lines:
        try:
            entries.append(json.loads(l))
        except Exception:
            pass
    if not entries:
        return
    high = sorted(entries, key=lambda e: -e.get("critique", {}).get("inspiration_score", 0))
    print(f"\n{'='*60}")
    print(f"Top {min(n, len(high))} directives from {len(entries)} critiques")
    print()
    for e in high[:n]:
        c = e.get("critique", {})
        score     = c.get("inspiration_score", "?")
        directive = c.get("priority_directive", "")
        refs      = c.get("named_references", "")
        vectors   = c.get("particle_art_vectors", "")
        print(f"  [{score}/10] {directive}")
        if refs:
            print(f"         refs: {refs[:80]}")
        if vectors:
            print(f"      vectors: {vectors[:100]}")
        print()


def main():
    ap = argparse.ArgumentParser(description="Pinterest inspiration pipeline")
    ap.add_argument("--board-url",    default=DEFAULT_BOARD)
    ap.add_argument("--limit",        type=int, default=15)
    ap.add_argument("--dry-run",      action="store_true",
                    help="Skip Bedrock VLM calls (use dummy critique output)")
    ap.add_argument("--skip-scrape",  action="store_true",
                    help="Skip Step 1 — use cached pinterest_pins.json")
    args = ap.parse_args()

    py = sys.executable

    # ── Step 1: Scrape ────────────────────────────────────────────────────────
    if not args.skip_scrape:
        rc = run_step(
            "Scraping Pinterest board",
            [py, "scripts/pinterest_scraper.py",
             "--board-url", args.board_url,
             "--limit", str(args.limit)],
        )
        if rc != 0:
            sys.exit(f"Scraping failed (exit {rc})")
    else:
        print("  (--skip-scrape: using cached pinterest_pins.json)")

    # ── Step 2: Critique ──────────────────────────────────────────────────────
    critic_cmd = [py, "scripts/pinterest_critic_agent.py",
                  "--limit", str(args.limit)]
    if args.dry_run:
        critic_cmd.append("--dry-run")

    rc = run_step("Running art critic agent", critic_cmd)
    if rc != 0:
        sys.exit(f"Critic failed (exit {rc})")

    # ── Step 2b: Persist seen pins ────────────────────────────────────────────
    if not args.dry_run:
        update_seen_pins()

    # ── Step 3: Summary ───────────────────────────────────────────────────────
    if not args.dry_run:
        top_directives()

    print("Pipeline complete.")


if __name__ == "__main__":
    main()
