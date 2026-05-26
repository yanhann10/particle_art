#!/usr/bin/env bash
# on_stop_drain.sh — Stop hook (async).
# If .drain_claims.json shows free pieces remaining, auto-launch
# wait_and_drain.sh in the background so the queue resumes after
# the Claude Code hourly limit resets (~60 min).
#
# Fires automatically via .claude/settings.json Stop hook.

REPO="${CLAUDE_PROJECT_DIR:-$HOME/git_repo/particle_art}"
VENV="$REPO/.venv"
PY="$VENV/bin/python3"
[ -x "$PY" ] || PY=python3

LOG="$REPO/.logs/drain-auto-$(date -u +%Y%m%d).log"
mkdir -p "$REPO/.logs"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

# Check if queue has free pieces left
FREE=$("$PY" "$REPO/scripts/drain_queue.py" --status 2>/dev/null \
       | grep -o "[0-9]* free" | grep -o "[0-9]*")

if [ "${FREE:-0}" -eq 0 ]; then
    exit 0  # nothing pending, don't schedule
fi

echo "[$(ts())] Session stopped with $FREE free piece(s) — scheduling wait_and_drain in background" >> "$LOG"

# Launch wait_and_drain detached so it survives the session ending
nohup bash "$REPO/scripts/wait_and_drain.sh" 60 >> "$LOG" 2>&1 &
disown $!

echo "[$(ts())] wait_and_drain.sh launched (PID $!)" >> "$LOG"
