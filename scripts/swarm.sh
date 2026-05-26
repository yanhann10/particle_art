#!/usr/bin/env bash
# swarm.sh — parallel workstream launcher for particle_art
#
# Two workstream types:
#   art  [N=2]            N concurrent mutation workers, each in its own tmux pane.
#                         Workers run mutate.py once; re-run the command to generate more.
#
#   feat "task1" "task2"  One Claude Code agent per task, each in an isolated
#                         git worktree + tmux pane. Agents commit their own work;
#                         you merge (or cherry-pick) when done.
#
# Other:
#   status                Show active panes and open worktrees.
#   kill                  Tear down the swarm session + remove worktrees.
#
# Requirements: tmux, git
# For art workers: .venv must exist at repo root (python3 + mutate.py deps).
# For feat agents: claude CLI must be in PATH.
#
# Examples:
#   ./scripts/swarm.sh art 3
#   ./scripts/swarm.sh feat "improve the blur gate" "add a per-piece click-to-full-screen"
#   ./scripts/swarm.sh status
#   ./scripts/swarm.sh kill

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$REPO/.venv"
SESSION="${PARTICLE_ART_SWARM_SESSION:-pax-swarm}"
SWARM_DIR="$REPO/.swarm"

# ── helpers ──────────────────────────────────────────────────────────────────

die()  { echo "error: $*" >&2; exit 1; }
log()  { echo "[swarm] $*"; }

_require_tmux() {
  command -v tmux >/dev/null 2>&1 || die "tmux not found — brew install tmux"
}

_require_venv() {
  [ -x "$VENV/bin/python3" ] || die "venv missing at $VENV — run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
}

_require_claude() {
  command -v claude >/dev/null 2>&1 || die "claude CLI not found — install Claude Code"
}

_ensure_session() {
  tmux has-session -t "$SESSION" 2>/dev/null || tmux new-session -d -s "$SESSION" -x 220 -y 50
}

# ── art workers ───────────────────────────────────────────────────────────────

_art() {
  local N="${1:-2}"
  _require_tmux
  _require_venv
  _ensure_session
  mkdir -p "$SWARM_DIR/logs"

  log "starting $N artwork workers in session '$SESSION' ..."
  for i in $(seq 1 "$N"); do
    local log_file="$SWARM_DIR/logs/art-$(date +%Y%m%d-%H%M%S)-w$i.log"
    local cmd="$VENV/bin/python3 $REPO/scripts/mutate.py 2>&1 | tee '$log_file'; echo; echo '── worker $i done (press any key to close) ──'; read -rn1"
    local win_name="art-$i"
    # First worker reuses the initial blank window
    if [ "$i" -eq 1 ] && tmux list-windows -t "$SESSION" 2>/dev/null | grep -q "^0:"; then
      tmux rename-window -t "$SESSION:0" "$win_name"
      tmux send-keys -t "$SESSION:$win_name" "$cmd" Enter
    else
      tmux new-window -t "$SESSION" -n "$win_name" "$cmd"
    fi
  done

  tmux select-window -t "$SESSION:art-1"
  log "$N art workers launched."
  log "  attach:  tmux attach -t $SESSION"
  log "  logs:    $SWARM_DIR/logs/"
}

# ── feature agents ────────────────────────────────────────────────────────────

_feat() {
  _require_tmux
  _require_claude
  _ensure_session
  mkdir -p "$SWARM_DIR/worktrees" "$SWARM_DIR/logs"

  local i=0
  for task in "$@"; do
    i=$((i + 1))
    local slug
    slug="feat-$i-$(date +%s)"
    local branch="swarm/$slug"
    local worktree="$SWARM_DIR/worktrees/$slug"

    # Create isolated worktree on a fresh branch from HEAD
    git -C "$REPO" worktree add "$worktree" -b "$branch" HEAD \
      || die "failed to create worktree for task $i"

    # Write the task brief so the agent (and you) can read it
    printf '%s\n' "$task" > "$worktree/TASK.md"

    local win_name="feat-$i"
    local preamble="Task #$i: $task"
    # claude reads TASK.md on first turn; --dangerously-skip-permissions lets it
    # run without approval prompts (safe: it's in an isolated worktree)
    local claude_prompt="Read TASK.md and implement the task. Commit your changes when done (do not push). Keep scope tight — only touch what the task asks."
    local cmd
    cmd="cd '$worktree' && echo '── $preamble ──' && claude --dangerously-skip-permissions '$claude_prompt'"

    tmux new-window -t "$SESSION" -n "$win_name" "$cmd"
    log "feat agent $i started: '$task' → branch $branch"
  done

  tmux select-window -t "$SESSION:feat-1" 2>/dev/null || true
  log "$i feature agents launched."
  log "  attach:  tmux attach -t $SESSION"
  log "  merge:   git -C $REPO merge swarm/feat-<slug>"
  log "  list:    git -C $REPO worktree list"
}

# ── status ────────────────────────────────────────────────────────────────────

_status() {
  echo "── tmux session '$SESSION' ──────────────────────────────"
  tmux list-windows -t "$SESSION" 2>/dev/null \
    | awk '{printf "  %s\n", $0}' \
    || echo "  (no session)"

  echo ""
  echo "── git worktrees ──────────────────────────────────────"
  git -C "$REPO" worktree list 2>/dev/null \
    | grep -E "(swarm|$REPO$)" \
    | awk '{printf "  %s\n", $0}' \
    || echo "  (none)"

  echo ""
  echo "── recent logs ────────────────────────────────────────"
  ls -t "$SWARM_DIR/logs/" 2>/dev/null | head -8 | awk '{printf "  %s\n", $0}' || echo "  (none)"
}

# ── kill ──────────────────────────────────────────────────────────────────────

_kill() {
  # Kill tmux session
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux kill-session -t "$SESSION"
    log "killed tmux session '$SESSION'"
  else
    log "no active session '$SESSION'"
  fi

  # Prune swarm worktrees (leaves non-swarm worktrees untouched)
  local pruned=0
  if [ -d "$SWARM_DIR/worktrees" ]; then
    for wt in "$SWARM_DIR/worktrees"/*/; do
      [ -d "$wt" ] || continue
      git -C "$REPO" worktree remove --force "$wt" 2>/dev/null && pruned=$((pruned + 1))
    done
    rmdir "$SWARM_DIR/worktrees" 2>/dev/null || true
  fi
  git -C "$REPO" worktree prune 2>/dev/null || true
  [ "$pruned" -gt 0 ] && log "removed $pruned worktree(s)"
}

# ── dispatch ──────────────────────────────────────────────────────────────────

case "${1:-}" in
  art)    shift; _art    "$@" ;;
  feat)   shift; _feat   "$@" ;;
  status) shift; _status "$@" ;;
  kill)   shift; _kill   "$@" ;;
  *)
    cat <<'EOF'
Usage:
  swarm.sh art  [N=2]          N artwork mutation workers
  swarm.sh feat "t1" "t2" ...  one feature agent per task (isolated worktree)
  swarm.sh status              show active panes and worktrees
  swarm.sh kill                tear down session + worktrees
EOF
    exit 1
    ;;
esac
