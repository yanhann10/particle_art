#!/usr/bin/env bash
# Autonomous evolve tick — every 45 min, Max-subscription-only, pushes to `staging`.
# Method (see eval_criteria.md): hypothesis -> time-boxed gen -> binary gate -> commit/rollback.
# Reuses the proven improv_tick.py (gen + critic + render gate + precheck).
set -u
REPO="${PARTICLE_ART_REPO:-$HOME/git_repo/particle_art}"
LOG_DIR="$REPO/.logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/evolve-$(date -u +%Y%m).log"
LOCK="/tmp/particle_art_evolve.lock"
export PARTICLE_ART_PROVIDER="${PARTICLE_ART_PROVIDER:-subscription}"
ts(){ date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log(){ echo "[$(ts)] $*" >> "$LOG"; }

exec 9>"$LOCK" || { log "FATAL: cannot open lock"; exit 1; }
if ! flock -n 9; then log "skipped: previous tick still running"; exit 0; fi

log "-- evolve tick start (provider=$PARTICLE_ART_PROVIDER) --"
cd "$REPO" || { log "FATAL: cannot cd $REPO"; exit 1; }

BRANCH="$(git branch --show-current)"
if [ "$BRANCH" != "staging" ]; then log "FATAL: not on staging (on $BRANCH)"; exit 1; fi

git pull --rebase -X theirs --autostash --quiet 2>>"$LOG" || log "WARN: pull failed (continuing)"

VENV="$REPO/.venv"
[ -x "$VENV/bin/python3" ] || { log "FATAL: venv missing"; exit 2; }

# Time-boxed experiment. improv_tick: hypothesis -> v1 -> critic -> (v2 -> critic) -> gate.
RC=0
OUTPUT=$(timeout 1200 "$VENV/bin/python3" "$REPO/scripts/improv_tick.py" --no-push 2>&1) || RC=$?
echo "$OUTPUT" | sed "s/^/  /" >> "$LOG"

# Detect subscription usage-limit -> clean skip, no spend, no hammer.
if echo "$OUTPUT" | grep -qiE "usage limit|subscription unavailable|rate.?limit|no bedrock fallback"; then
  log "skipped: Max usage limit reached — backing off until next tick"; log "-- tick end (limit) --"; exit 0
fi

if [ "$RC" -eq 0 ]; then
  for attempt in 1 2 3; do
    if git push --quiet origin staging 2>>"$LOG"; then log "push OK (attempt $attempt) -> staging"; break; fi
    log "push attempt $attempt failed; rebasing on origin/staging"
    git fetch --quiet origin 2>>"$LOG" || true
    git rebase -X theirs origin/staging --quiet 2>>"$LOG" || { log "rebase failed; abort"; git rebase --abort 2>/dev/null; RC=7; break; }
    sleep $((RANDOM % 5 + 1))
  done
fi

case "$RC" in
  0) log "tick OK (committed + pushed to staging)";;
  2) log "tick skipped: budget cap";;
  4) log "tick skipped: provider failure (likely Max limit) — no commit";;
  7) log "tick rejected by gate/critic (rolled back, no commit)";;
  124) log "tick TIMED OUT (>20min) — rolled back";;
  *) log "tick FAILED rc=$RC";;
esac
ls -t "$LOG_DIR"/evolve-*.log 2>/dev/null | tail -n +9 | xargs -r rm -f
log "-- tick end (rc=$RC) --"
exit 0
