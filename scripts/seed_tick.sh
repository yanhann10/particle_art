#!/usr/bin/env bash
# Seed-driven mutation cron entry. Channels a specific seed image's artist DNA
# (e.g. seeds/img9245 = Chiharu Shiota at Asian Art Museum SF) into the
# evolving particle-art lineage on its own branch — parallel to the regular
# parallel_tick.sh / improv_cron.sh loops.
#
# Each tick:
#   1. Pulls latest (rebase -X theirs)
#   2. Picks a parent: most recent piece descended from the seed branch (by
#      directives_in_lineage containing 'seed_channel'); falls back to a
#      sensible non-seed favorite if none exists yet.
#   3. Calls mutate.py --seed seeds/<slug> --parent <id>
#      (mutate.py reads seed meta + artist SKILL.md and injects them.)
#
# Crontab example (every 15 min, alongside */10 parallel_tick.sh + */12 improv):
#   */15 * * * * SEED=img9245 /home/ubuntu/git_repo/particle_art/scripts/seed_tick.sh

set -u
SEED="${SEED:-img9245}"
REPO="${PARTICLE_ART_REPO:-$HOME/git_repo/particle_art}"
LOG_DIR="$REPO/.logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/seed-$(date -u +%Y%m).log"
LOCK="$REPO/.seed_tick.${SEED}.lock"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[$(ts)] [seed:$SEED] $*" >> "$LOG"; }

exec 9>"$LOCK"
flock -n 9 || { log "another instance running, skip"; exit 0; }

log "── seed_tick start ──"
cd "$REPO" || { log "FATAL: cannot cd to $REPO"; exit 1; }

if ! git pull --rebase -X theirs --autostash --quiet 2>>"$LOG"; then
  log "WARN: pre-pull failed"
fi

VENV="$REPO/.venv"
[ -x "$VENV/bin/python3" ] || { log "FATAL: venv missing"; exit 2; }

SEED_DIR="seeds/$SEED"
# Shiota seed (img9245) was removed 2026-06-08 (culled red-thread aesthetic). With no
# seeds left, this tick is a clean no-op rather than a cron error. Re-add a seed dir to revive.
[ -f "$REPO/$SEED_DIR/meta.json" ] || { log "no seed '$SEED' (seeds removed) — skipping"; exit 0; }

# parent picker — descend from the seed branch if any descendants exist
PARENT=$("$VENV/bin/python3" - <<PYEOF
import json, sys
from pathlib import Path
repo = Path("$REPO")
lin = json.loads((repo / "lineage.json").read_text())
seed_pieces = []
for p in lin["pieces"]:
    meta = repo / "pieces" / p["id"] / "meta.json"
    if not meta.exists(): continue
    try:
        m = json.loads(meta.read_text())
    except Exception:
        continue
    if m.get("seed") == "$SEED" or "seed_channel" in (m.get("directives_in_lineage") or []):
        seed_pieces.append((p, m))
# prefer the most recent seed-branch leaf
if seed_pieces:
    seed_pieces.sort(key=lambda x: x[0].get("created_at",""), reverse=True)
    print(seed_pieces[0][0]["id"])
else:
    # bootstrap: use a favorite that's open to install-style mutation
    # 5gm = differential growth tower (vertical, lit, suspendable into a web)
    # 7ea = differential growth coral (network-like)
    # ere = full atmospheric environment (Riley pattern in inhabited space)
    # pick the most recent of these still in lineage
    ids = {p["id"] for p in lin["pieces"]}
    for candidate in ("ere", "5gm", "7ea", "ajo"):
        if candidate in ids:
            print(candidate); sys.exit(0)
    print(lin["pieces"][-1]["id"])
PYEOF
)

log "parent=$PARENT  seed=$SEED_DIR"

OUTPUT=$("$VENV/bin/python3" "$REPO/scripts/mutate.py" --seed "$SEED_DIR" --parent "$PARENT" 2>&1) || RC=$?
RC="${RC:-0}"
echo "$OUTPUT" | sed "s/^/  /" >> "$LOG"
log "── seed_tick end (rc=$RC) ──"
exit 0
