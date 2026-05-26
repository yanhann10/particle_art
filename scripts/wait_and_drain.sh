#!/usr/bin/env bash
# wait_and_drain.sh — wait for Claude Code hourly limit to reset, then
# automatically resume drain_queue for the next batch.
#
# Run this in a terminal when you hit the hourly limit:
#   bash scripts/wait_and_drain.sh          # wait 60 min (default)
#   bash scripts/wait_and_drain.sh 45       # wait 45 min
#   bash scripts/wait_and_drain.sh 0        # skip wait, drain now
#
# What it does:
#   1. Waits WAIT_MINUTES
#   2. macOS notification when ready
#   3. Runs drain_queue.sh (picks next unclaimed batch from .drain_claims.json)
#   4. Loops: after each batch completes, checks if queue has more and repeats
#
# This script is intentionally Claude-free — the Python mutation workers run
# independently. It just manages the queue drain loop in the background.

WAIT_MINUTES=${1:-60}
REPO="${PARTICLE_ART_REPO:-$HOME/git_repo/particle_art}"
VENV="$REPO/.venv"
PY="$VENV/bin/python3"
[ -x "$PY" ] || PY=python3

DRAIN_N="${DRAIN_N:-4}"   # workers per batch (override: DRAIN_N=3 bash wait_and_drain.sh)
DRAIN_C="${DRAIN_C:-1}"   # critic slot (0 to skip)

notify() {
    local msg="$1"
    osascript -e "display notification \"$msg\" with title \"particle_art\" sound name \"Ping\"" 2>/dev/null || true
    echo "$msg"
}

if [ "$WAIT_MINUTES" -gt 0 ]; then
    echo "Waiting ${WAIT_MINUTES} min for Claude Code limit reset…"
    echo "Queue will auto-resume in particle_art. Press Ctrl+C to cancel."
    sleep $((WAIT_MINUTES * 60))
    notify "Hourly limit reset — resuming drain queue"
fi

# Drain loop — keep going until queue is empty or all claimed
ROUND=0
while true; do
    ROUND=$((ROUND + 1))
    echo ""
    echo "━━━ drain round $ROUND ━━━"

    # Check if anything is left
    REMAINING=$("$PY" "$REPO/scripts/drain_queue.py" --status 2>/dev/null | grep "free" | grep -o "[0-9]* free" | grep -o "[0-9]*")
    if [ "${REMAINING:-0}" -eq 0 ]; then
        notify "drain_queue: all pieces done ✓"
        echo "Queue empty — all done."
        break
    fi

    echo "$REMAINING piece(s) remaining — starting batch of $DRAIN_N…"
    bash "$REPO/scripts/drain_queue.sh" -n "$DRAIN_N" -c "$DRAIN_C"

    # Brief pause between rounds so rate limits settle
    sleep 10
done
