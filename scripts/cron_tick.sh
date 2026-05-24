#!/usr/bin/env bash
# Cron entry point. Pulls latest, runs one mutation tick, logs outcome.
# Designed to be safe to run from cron (no interactive output, captures errors,
# never aborts the host script if the inner command fails).
#
# Install crontab line example (every 6 hours at :17):
#   17 */6 * * *  /home/ubuntu/git_repo/particle_art/scripts/cron_tick.sh
#
# evaluator.py should be called after each tick to score new pieces and
# queue feedback into eval_queue.jsonl for the next tick to consume.
# Suggested crontab line (runs 2 min after the tick, giving git time to settle):
#   19 */6 * * *  cd /home/ubuntu/git_repo/particle_art && .venv/bin/python3 scripts/evaluator.py

set -u
REPO="${PARTICLE_ART_REPO:-$HOME/git_repo/particle_art}"
LOG_DIR="$REPO/.logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/cron-$(date -u +%Y%m).log"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

log "── tick start ──"
cd "$REPO" || { log "FATAL: cannot cd to $REPO"; exit 1; }

# always pull first; cron should never advance from a stale local.
# rebase + theirs preserves locally-generated mutation commits while resolving
# binary thumbnail conflicts in CI's favor (CI is the authority on rendered PNGs).
if ! git pull --rebase -X theirs --autostash --quiet 2>>"$LOG"; then
  log "WARN: git pull --rebase failed (continuing on local state)"
fi

VENV="$REPO/.venv"
if [ ! -x "$VENV/bin/python3" ]; then
  log "FATAL: venv missing at $VENV — run: python3 -m venv .venv && .venv/bin/pip install boto3"
  exit 2
fi

# run mutation tick; capture output
OUTPUT=$("$VENV/bin/python3" "$REPO/scripts/mutate.py" 2>&1) || RC=$?
RC="${RC:-0}"
echo "$OUTPUT" | sed "s/^/  /" >> "$LOG"

if [ "$RC" -eq 0 ]; then
  log "tick OK"
elif [ "$RC" -eq 2 ]; then
  log "tick skipped: budget cap"
else
  log "tick FAILED rc=$RC"
fi

# log rotation: keep last 8 monthly files
ls -t "$LOG_DIR"/cron-*.log 2>/dev/null | tail -n +9 | xargs -r rm -f
log "── tick end (rc=$RC) ──"
exit 0
