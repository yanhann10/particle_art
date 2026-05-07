#!/usr/bin/env bash
# Cron entry point for the improv tick. Designed for *every-12-min* cadence
# (≈ 120 ticks/day). Uses `flock` so an over-running tick (e.g. slow Claude
# response, big git push) doesn't overlap with the next tick.
#
# Install crontab line:
#   */12 * * * *  /home/ubuntu/git_repo/particle_art/scripts/improv_cron.sh
#
# Env knobs (set in crontab line if needed):
#   PARTICLE_ART_DAILY_RUNS=140   # cap higher than expected 120 + 8 + 4
#   PARTICLE_ART_DAILY_CAP=2.00   # USD cap (only relevant if subscription falls through)

set -u
REPO="${PARTICLE_ART_REPO:-$HOME/git_repo/particle_art}"
LOG_DIR="$REPO/.logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/improv-$(date -u +%Y%m).log"
LOCK="/tmp/particle_art_improv.lock"

ts()  { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

# nonblocking lock — if the previous tick is still running, just skip
exec 9>"$LOCK" || { log "FATAL: cannot open lock"; exit 1; }
if ! flock -n 9; then
  log "skipped: previous improv tick still running"
  exit 0
fi

log "── improv tick start ──"
cd "$REPO" || { log "FATAL: cannot cd to $REPO"; exit 1; }

# ALWAYS pull first; cron should never advance from a stale local. Use the
# same strategy as parallel_tick.sh — multi-cron contention is real.
if ! git pull --rebase -X theirs --autostash --quiet 2>>"$LOG"; then
  log "WARN: git pull failed (continuing on local state)"
fi

VENV="$REPO/.venv"
if [ ! -x "$VENV/bin/python3" ]; then
  log "FATAL: venv missing at $VENV — run: python3 -m venv .venv && .venv/bin/pip install boto3"
  exit 2
fi

# improv_tick.py does the gen+commit. We pass --no-push so it commits locally
# but doesn't push; the retry loop below handles push contention with the
# */10 parallel_tick + 3h mutate crons that share this repo.
OUTPUT=$("$VENV/bin/python3" "$REPO/scripts/improv_tick.py" --no-push 2>&1) || RC=$?
RC="${RC:-0}"
echo "$OUTPUT" | sed "s/^/  /" >> "$LOG"

# Push with retry. Up to 3 attempts: if rejected, fetch+rebase (theirs),
# sleep a small random jitter to de-correlate from sibling crons, retry.
if [ "$RC" -eq 0 ]; then
  for attempt in 1 2 3; do
    if git push --quiet 2>>"$LOG"; then
      log "push OK on attempt $attempt"
      break
    fi
    log "push attempt $attempt failed; rebasing on origin/main"
    git fetch --quiet origin 2>>"$LOG" || true
    if ! git rebase -X theirs origin/main --quiet 2>>"$LOG"; then
      log "rebase failed; aborting and giving up on this tick"
      git rebase --abort 2>/dev/null || true
      RC=7
      break
    fi
    sleep $((RANDOM % 5 + 1))
  done
fi

if [ "$RC" -eq 0 ]; then
  log "tick OK"
elif [ "$RC" -eq 2 ]; then
  log "tick skipped: budget cap"
else
  log "tick FAILED rc=$RC"
fi

# log rotation: keep last 8 monthly files
ls -t "$LOG_DIR"/improv-*.log 2>/dev/null | tail -n +9 | xargs -r rm -f
log "── tick end (rc=$RC) ──"
exit 0
