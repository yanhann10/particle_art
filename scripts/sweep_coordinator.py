#!/usr/bin/env python3
"""
Hyperparameter sweep job coordinator.

Manages manifest for parallel optimization sessions. Each session claims 5 pieces
atomically (via lock file) and marks them as optimizing/done to prevent overlap.

Usage:
  python3 sweep_coordinator.py init              # Populate manifest with all pieces
  python3 sweep_coordinator.py claim <session>   # Claim next 5 pending pieces
  python3 sweep_coordinator.py mark <piece_id> done [--notes TEXT]
  python3 sweep_coordinator.py status            # Show manifest summary
"""

import sys
import json
import time
import fcntl
import os
from pathlib import Path
from datetime import datetime, timezone

MANIFEST_PATH = Path.home() / "git_repo" / "particle_art" / ".sweep_manifest.json"
LOCK_FILE = MANIFEST_PATH.parent / ".sweep_manifest.lock"
PIECES_DIR = Path.home() / "git_repo" / "particle_art" / "pieces"

def acquire_lock(timeout=10):
    """Acquire exclusive lock on manifest file."""
    start = time.time()
    while True:
        try:
            lock_fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            return lock_fd
        except FileExistsError:
            if time.time() - start > timeout:
                raise TimeoutError(f"Could not acquire lock within {timeout}s")
            time.sleep(0.1)

def release_lock(lock_fd):
    """Release exclusive lock."""
    os.close(lock_fd)
    LOCK_FILE.unlink()

def load_manifest():
    """Load manifest from disk."""
    if not MANIFEST_PATH.exists():
        return {"metadata": {}, "pieces": {}}
    with open(MANIFEST_PATH) as f:
        return json.load(f)

def save_manifest(manifest):
    """Save manifest to disk."""
    with open(MANIFEST_PATH, 'w') as f:
        json.dump(manifest, f, indent=2)

def cmd_init():
    """Populate manifest with all pieces from pieces/ directory."""
    lock_fd = acquire_lock()
    try:
        manifest = load_manifest()

        # Scan pieces directory
        if not PIECES_DIR.exists():
            print(f"Error: {PIECES_DIR} not found")
            return 1

        all_pieces = sorted([p.name for p in PIECES_DIR.iterdir() if p.is_dir()])
        print(f"Found {len(all_pieces)} pieces")

        # Add to manifest
        for piece_id in all_pieces:
            if piece_id not in manifest["pieces"]:
                manifest["pieces"][piece_id] = {
                    "status": "pending",
                    "session": None,
                    "timestamp": None,
                    "notes": None
                }

        manifest["metadata"]["last_init"] = datetime.now(timezone.utc).isoformat()
        manifest["metadata"]["total_pieces"] = len(all_pieces)
        save_manifest(manifest)

        print(f"Manifest initialized with {len(all_pieces)} pieces")
        return 0
    finally:
        release_lock(lock_fd)

def cmd_claim(session_id):
    """Claim next 5 pending pieces for a session."""
    lock_fd = acquire_lock()
    try:
        manifest = load_manifest()
        pieces = manifest.get("pieces", {})

        # Find up to 5 pending pieces
        claimed = []
        for piece_id in sorted(pieces.keys()):
            if len(claimed) >= 5:
                break
            if pieces[piece_id]["status"] == "pending":
                pieces[piece_id]["status"] = "optimizing"
                pieces[piece_id]["session"] = session_id
                pieces[piece_id]["timestamp"] = datetime.now(timezone.utc).isoformat()
                claimed.append(piece_id)

        save_manifest(manifest)

        if claimed:
            print(f"Session {session_id} claimed: {' '.join(claimed)}")
            print('\n'.join(claimed))  # For easy consumption by shell
        else:
            print(f"No pending pieces available")

        return 0
    finally:
        release_lock(lock_fd)

def cmd_mark(piece_id, status, notes=None):
    """Mark a piece with new status."""
    lock_fd = acquire_lock()
    try:
        manifest = load_manifest()
        pieces = manifest.get("pieces", {})

        if piece_id not in pieces:
            print(f"Error: {piece_id} not found in manifest")
            return 1

        pieces[piece_id]["status"] = status
        pieces[piece_id]["timestamp"] = datetime.now(timezone.utc).isoformat()
        if notes:
            pieces[piece_id]["notes"] = notes

        save_manifest(manifest)
        print(f"Marked {piece_id} as {status}")
        return 0
    finally:
        release_lock(lock_fd)

def cmd_status():
    """Show manifest summary."""
    manifest = load_manifest()
    pieces = manifest.get("pieces", {})

    if not pieces:
        print("Manifest empty")
        return 0

    status_counts = {}
    for p in pieces.values():
        s = p["status"]
        status_counts[s] = status_counts.get(s, 0) + 1

    total = len(pieces)
    print(f"Total: {total}")
    for status in ["pending", "optimizing", "done"]:
        count = status_counts.get(status, 0)
        pct = 100 * count / total if total > 0 else 0
        print(f"  {status}: {count} ({pct:.1f}%)")

    return 0

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "init":
        sys.exit(cmd_init())
    elif cmd == "claim":
        if len(sys.argv) < 3:
            print("Usage: sweep_coordinator.py claim <session>")
            sys.exit(1)
        sys.exit(cmd_claim(sys.argv[2]))
    elif cmd == "mark":
        if len(sys.argv) < 4:
            print("Usage: sweep_coordinator.py mark <piece_id> <status> [--notes TEXT]")
            sys.exit(1)
        piece_id = sys.argv[2]
        status = sys.argv[3]
        notes = None
        if len(sys.argv) > 5 and sys.argv[4] == "--notes":
            notes = sys.argv[5]
        sys.exit(cmd_mark(piece_id, status, notes))
    elif cmd == "status":
        sys.exit(cmd_status())
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
