#!/usr/bin/env bash
# Mutation fanout. Spawns up to N mutate.py workers concurrently.
# Each worker independently picks parent + directive, calls Claude (subscription = $0),
# writes its piece, and serializes only the git critical section via flock.
#
# Keep fanout low by default; Bedrock fallback is paid per token.
# Each worker calls safe_push() (mutate.py) which already retries with rebase -X theirs.
#
# Crontab example (daily, 1 piece per tick):
#   0 3 * * *  /home/ubuntu/git_repo/particle_art/scripts/parallel_tick.sh

set -u
N="${1:-1}"
MAX_PARALLEL="${PARTICLE_ART_MAX_PARALLEL:-1}"
if [ "$N" -gt "$MAX_PARALLEL" ]; then
  N="$MAX_PARALLEL"
fi
REPO="${PARTICLE_ART_REPO:-$HOME/git_repo/particle_art}"
LOG_DIR="$REPO/.logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/cron-$(date -u +%Y%m).log"
LOCK="$REPO/.parallel_tick.lock"
THRUM="$HOME/.local/bin/thrum"

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

# --- thrum: process pending steering messages (keep/drop/mutate directives) ---
# Graceful: if daemon is down, skip silently — never block a tick.
if [ -x "$THRUM" ] && "$THRUM" daemon status --quiet 2>/dev/null; then
  INBOX=$("$THRUM" inbox --unread --json 2>/dev/null || echo '[]')
  MSG_COUNT=$(echo "$INBOX" | "$VENV/bin/python3" -c "import json,sys; d=json.load(sys.stdin); print(len(d) if isinstance(d,list) else len(d.get('messages',[])))" 2>/dev/null || echo 0)
  if [ "$MSG_COUNT" -gt 0 ]; then
    log "thrum: processing $MSG_COUNT message(s)"
    echo "$INBOX" | "$VENV/bin/python3" "$REPO/scripts/thrum_inbox.py" 2>>"$LOG" || log "WARN: thrum_inbox.py error (continuing)"
  fi
fi

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

# --- thrum: report tick completion ---
if [ -x "$THRUM" ] && "$THRUM" daemon status --quiet 2>/dev/null; then
  NEW_COUNT=$(git log --oneline ORIG_HEAD..HEAD -- 'pieces/' 2>/dev/null | wc -l | tr -d ' ' || echo '?')
  "$THRUM" send "tick done — ~${NEW_COUNT} new piece(s). check gallery." --to @human 2>/dev/null || true
fi

exit 0
