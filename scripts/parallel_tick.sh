#!/usr/bin/env bash
# Parallel mutation fanout. Spawns N mutate.py workers concurrently.
# Each worker independently picks parent + directive, calls Claude (subscription = $0),
# writes its piece, and serializes only the git critical section via flock.
#
# Subscription cost = $0/call, so concurrency is bounded by Claude rate-limits, not budget.
# Each worker calls safe_push() (mutate.py) which already retries with rebase -X theirs.
#
# Crontab example (every 10 min, 3 parallel pieces per tick = 18/hour mutation throughput):
#   */10 * * * *  /home/ubuntu/git_repo/particle_art/scripts/parallel_tick.sh 3

set -u
N="${1:-3}"
REPO="${PARTICLE_ART_REPO:-$HOME/git_repo/particle_art}"
LOG_DIR="$REPO/.logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/cron-$(date -u +%Y%m).log"
LOCK="$REPO/.parallel_tick.lock"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

# session-level lock so two parallel_tick.sh invocations don't pile on each other
exec 9>"$LOCK"
flock -n 9 || { log "parallel_tick: another instance running, skip"; exit 0; }

log "── parallel_tick start (N=$N) ──"
cd "$REPO" || { log "FATAL: cannot cd to $REPO"; exit 1; }

if ! git pull --rebase -X theirs --autostash --quiet 2>>"$LOG"; then
  log "WARN: pre-pull failed (continuing on local state)"
fi

VENV="$REPO/.venv"
[ -x "$VENV/bin/python3" ] || { log "FATAL: venv missing at $VENV"; exit 2; }

PIDS=()
for i in $(seq 1 "$N"); do
  ( "$VENV/bin/python3" "$REPO/scripts/mutate.py" 2>&1 \
      | sed "s/^/  [w$i] /" >> "$LOG" ) &
  PIDS+=("$!")
  # tiny stagger so two workers don't race on lineage.json read at the exact same ms
  sleep 1
done

for pid in "${PIDS[@]}"; do
  wait "$pid" || true
done

# final backlog drain — if any worker's push got rejected after retry, this catches it
git push 2>>"$LOG" || git pull --rebase -X theirs --autostash --quiet 2>>"$LOG" && git push 2>>"$LOG" || true

log "── parallel_tick end ──"
exit 0
