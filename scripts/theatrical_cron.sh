#!/usr/bin/env bash
# Cron entry point for the theatrical-movement experiment.
# Runs theatrical_tick.py against the configured target piece.
#
# Install crontab line example (every 6h at :30 past, offset from the
# main mutation cron at :00 to avoid GitHub Actions race):
#   30 */6 * * *  /home/ubuntu/git_repo/particle_art/scripts/theatrical_cron.sh

set -u
REPO="${PARTICLE_ART_REPO:-$HOME/git_repo/particle_art}"
TARGET="${PARTICLE_ART_THEATRICAL_TARGET:-5gm}"
LOG_DIR="$REPO/.logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/theatrical-$(date -u +%Y%m).log"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

log "── theatrical tick (target=$TARGET) start ──"
cd "$REPO" || { log "FATAL: cannot cd to $REPO"; exit 1; }

if ! git pull --ff-only --quiet 2>>"$LOG"; then
  log "WARN: git pull failed (continuing on local state)"
fi

VENV="$REPO/.venv"
if [ ! -x "$VENV/bin/python3" ]; then
  log "FATAL: venv missing at $VENV"
  exit 2
fi

OUTPUT=$("$VENV/bin/python3" "$REPO/scripts/theatrical_tick.py" "$TARGET" 2>&1) || RC=$?
RC="${RC:-0}"
echo "$OUTPUT" | sed "s/^/  /" >> "$LOG"

if [ "$RC" -eq 0 ]; then
  log "tick OK"
elif [ "$RC" -eq 2 ]; then
  log "tick skipped: budget cap"
elif [ "$RC" -eq 7 ]; then
  log "tick rejected by render gate (no commit, no push)"
else
  log "tick FAILED rc=$RC"
fi

ls -t "$LOG_DIR"/theatrical-*.log 2>/dev/null | tail -n +8 | xargs -r rm -f
log "── tick end (rc=$RC) ──"
exit 0
