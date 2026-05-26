#!/usr/bin/env bash
# drain_queue.sh — thin wrapper around drain_queue.py
#
# Usage:
#   bash scripts/drain_queue.sh              # 4 mutators + 1 critic
#   bash scripts/drain_queue.sh -n 3         # 3 mutators + 1 critic
#   bash scripts/drain_queue.sh -n 4 -c 0    # 4 mutators, no critic
#   bash scripts/drain_queue.sh --status     # show what's claimed
#
# Cross-session: claim file .drain_claims.json tracks in-progress pieces.
# Run again in a new tab → picks the next unclaimed batch automatically.

REPO="${PARTICLE_ART_REPO:-$HOME/git_repo/particle_art}"
VENV="$REPO/.venv"
PY="$VENV/bin/python3"
[ -x "$PY" ] || PY=python3

exec "$PY" "$REPO/scripts/drain_queue.py" "$@"
