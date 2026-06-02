#!/usr/bin/env python3
"""
Generate default hyperparam.json for pieces lacking one.

Creates a minimal safe config that tests piece stability without parametrization.
Useful for getting sweep infrastructure running on all 1000+ pieces.

Usage:
  python3 generate_default_hyperparam.py [--force]
    --force: Overwrite existing hyperparam.json files
"""

import sys
import json
from pathlib import Path

PIECES_DIR = Path.home() / "git_repo" / "particle_art" / "pieces"

DEFAULT_CONFIG = {
    "description": "Auto-generated: validation-only mode (no parametrization). Takes screenshot, computes metrics.",
    "grid": {},
    "warmup_ms": 5000,
    "constraints": {
        "min_sharpness": 0,
        "min_stddev": 0,
        "visual_distinctness_threshold": 0.15
    }
}

def main():
    force = "--force" in sys.argv

    if not PIECES_DIR.exists():
        print(f"Error: {PIECES_DIR} not found")
        return 1

    pieces = sorted([p for p in PIECES_DIR.iterdir() if p.is_dir()])
    created = 0
    skipped = 0

    for piece_dir in pieces:
        piece_id = piece_dir.name
        config_file = piece_dir / "hyperparam.json"

        if config_file.exists() and not force:
            skipped += 1
            continue

        with open(config_file, 'w') as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        created += 1

        if created % 100 == 0:
            print(f"  Generated {created}...")

    print(f"\nGenerated {created} hyperparam.json files")
    print(f"Skipped {skipped} (already exist)")
    return 0

if __name__ == '__main__':
    sys.exit(main())
