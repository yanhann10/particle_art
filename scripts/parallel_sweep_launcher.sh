#!/bin/bash
#
# Parallel hyperparameter sweep launcher.
#
# Usage:
#   bash parallel_sweep_launcher.sh <session_id>
#
# Opens one terminal session. Each session claims 5 pieces, spawns 5 agents
# in parallel, waits for completion, marks pieces done, repeats.
# Max 10 sessions × 5 pieces = 50 pieces per sweep cycle.
#
# Example (run in 10 separate terminals):
#   for i in {1..10}; do
#     osascript -e "tell app \"Terminal\" to do script \"cd ~/git_repo/particle_art && bash scripts/parallel_sweep_launcher.sh session_$i\""
#   done

set -euo pipefail

SESSION_ID="${1:?Usage: parallel_sweep_launcher.sh <session_id>}"
REPO_DIR="${HOME}/git_repo/particle_art"
SCRIPTS_DIR="${REPO_DIR}/scripts"
COORDINATOR="${SCRIPTS_DIR}/sweep_coordinator.py"
SKILL_DIR="${HOME}/.claude/skills/hyperparam-sweep"

cd "$REPO_DIR"

echo "=== Session $SESSION_ID starting ==="

# Initialize manifest if needed (idempotent)
if [ ! -f .sweep_manifest.json ] || [ "$(jq '.pieces | length' .sweep_manifest.json)" -eq 0 ]; then
    echo "Initializing manifest..."
    python3 "$COORDINATOR" init
fi

# Main loop: claim pieces, optimize, mark done
while true; do
    echo ""
    echo "[$SESSION_ID] Claiming next 5 pieces..."
    PIECES=$(python3 "$COORDINATOR" claim "$SESSION_ID" | tail -n +2)

    if [ -z "$PIECES" ]; then
        echo "[$SESSION_ID] No more pieces to claim. Exiting."
        break
    fi

    PIECE_ARRAY=($PIECES)
    echo "[$SESSION_ID] Claimed: ${PIECE_ARRAY[@]}"

    # Spawn 5 agents in parallel (one per piece)
    declare -A PIDS
    for PIECE_ID in "${PIECE_ARRAY[@]}"; do
        (
            echo "[$SESSION_ID] Starting sweep for $PIECE_ID..."
            if bash "$SKILL_DIR/sweep.sh" "$PIECE_ID" > "/tmp/sweep_${PIECE_ID}.log" 2>&1; then
                echo "[$SESSION_ID] ✓ $PIECE_ID completed"
                python3 "$COORDINATOR" mark "$PIECE_ID" done
            else
                echo "[$SESSION_ID] ✗ $PIECE_ID failed (see /tmp/sweep_${PIECE_ID}.log)"
                python3 "$COORDINATOR" mark "$PIECE_ID" done --notes "sweep failed"
            fi
        ) &
        PIDS[$PIECE_ID]=$!
    done

    # Wait for all agents to finish
    echo "[$SESSION_ID] Waiting for ${#PIDS[@]} sweeps to complete..."
    for PIECE_ID in "${!PIDS[@]}"; do
        wait ${PIDS[$PIECE_ID]} || true
    done

    echo "[$SESSION_ID] Batch complete. Continuing..."
done

echo "=== Session $SESSION_ID exited ==="
