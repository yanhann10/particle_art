#!/usr/bin/env python3
"""
drain_queue.py — drain iterate_when_chosen with N parallel mutation workers + 1 critic.

Cross-session coordination via .drain_claims.json:
  - Each invocation claims N pieces, then spawns workers
  - Stale claims (dead PIDs) are auto-released on each run
  - New tab / new session → picks next N unclaimed pieces automatically

Usage:
    python3 scripts/drain_queue.py              # 4 mutators + 1 critic
    python3 scripts/drain_queue.py -n 3         # 3 mutators + 1 critic
    python3 scripts/drain_queue.py -n 4 -c 0    # 4 mutators, skip critic
    python3 scripts/drain_queue.py --status     # show claim state, don't run
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO       = Path(__file__).resolve().parent.parent
VENV_PY    = REPO / ".venv" / "bin" / "python3"
CLAIMS     = REPO / ".drain_claims.json"
TASTE      = REPO / "taste.json"
LOG_DIR    = REPO / ".logs"
TELEGRAM_ENV = Path.home() / ".particle_telegram.env"


# ── helpers ───────────────────────────────────────────────────────────────────

def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def log(msg: str) -> None:
    print(f"[{ts()}] {msg}", flush=True)

def py() -> str:
    return str(VENV_PY) if VENV_PY.exists() else sys.executable


# ── claim file ────────────────────────────────────────────────────────────────

def _load_claims() -> dict:
    if CLAIMS.exists():
        try:
            return json.loads(CLAIMS.read_text()).get("claimed", {})
        except Exception:
            pass
    return {}

def _save_claims(claimed: dict) -> None:
    CLAIMS.write_text(json.dumps({"claimed": claimed}, indent=2) + "\n")

def _clean_stale(claimed: dict) -> dict:
    """Drop claims whose worker PID is gone; keep placeholder (pid=0) for <60s."""
    live = {}
    now = datetime.now(timezone.utc)
    for piece, meta in claimed.items():
        pid = meta.get("pid", 0)
        if pid and pid != 0:
            try:
                os.kill(pid, 0)   # 0 = just check existence
                live[piece] = meta
            except OSError:
                pass              # dead — release
        else:
            try:
                claimed_at = datetime.fromisoformat(meta["claimed_at"].replace("Z", "+00:00"))
                if (now - claimed_at).total_seconds() < 60:
                    live[piece] = meta
            except Exception:
                pass
    return live

def claim_next(n: int) -> list[str]:
    claimed = _clean_stale(_load_claims())
    taste   = json.loads(TASTE.read_text())
    queue   = list(taste.get("iterate_when_chosen", {}).keys())
    free    = [p for p in queue if p not in claimed][:n]
    if not free:
        return []
    now_iso = datetime.now(timezone.utc).isoformat()
    for p in free:
        claimed[p] = {"pid": 0, "claimed_at": now_iso}
    _save_claims(claimed)
    return free

def update_pids(piece_pids: dict[str, int]) -> None:
    claimed = _load_claims()
    for p, pid in piece_pids.items():
        if p in claimed:
            claimed[p]["pid"] = pid
    _save_claims(claimed)

def release(pieces: list[str]) -> None:
    claimed = _load_claims()
    for p in pieces:
        claimed.pop(p, None)
    _save_claims(claimed)

def status() -> None:
    claimed = _clean_stale(_load_claims())
    taste   = json.loads(TASTE.read_text())
    queue   = list(taste.get("iterate_when_chosen", {}).keys())
    free    = [p for p in queue if p not in claimed]
    print(f"\n{'PIECE':<6}  {'STATUS':<12}  PID")
    print("-" * 34)
    for p in queue:
        if p in claimed:
            pid = claimed[p].get("pid", 0)
            alive = False
            if pid:
                try: os.kill(pid, 0); alive = True
                except OSError: pass
            status_str = f"running ({pid})" if alive else f"claimed/stale"
            print(f"  {p:<6}  {status_str}")
        else:
            print(f"  {p:<6}  free")
    print(f"\n{len(claimed)} claimed, {len(free)} free, {len(queue)} total in queue.")


# ── telegram ──────────────────────────────────────────────────────────────────

def notify_telegram(pieces: list[str]) -> None:
    if not TELEGRAM_ENV.exists():
        return
    env = os.environ.copy()
    for line in TELEGRAM_ENV.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    subprocess.run(
        [py(), str(REPO / "scripts" / "notify_telegram.py")] + pieces,
        env=env, cwd=str(REPO), capture_output=True,
    )


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", "--workers", type=int, default=4,
                    help="number of parallel mutation workers (default 4)")
    ap.add_argument("-c", "--critic", type=int, default=1, choices=[0, 1],
                    help="1=run evaluator critic slot (default), 0=skip")
    ap.add_argument("--status", action="store_true",
                    help="print claim state without running")
    args = ap.parse_args()

    if args.status:
        status()
        return

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    pieces = claim_next(args.workers)
    if not pieces:
        log("Queue empty or all pieces already claimed — nothing to do.")
        status()
        return

    log(f"Claimed {len(pieces)} piece(s): {' '.join(pieces)}")

    procs: list[tuple[str, subprocess.Popen]] = []
    piece_pids: dict[str, int] = {}

    for piece in pieces:
        log_path = LOG_DIR / f"mutate-{piece}-{datetime.now(timezone.utc).strftime('%H%M%S')}.log"
        p = subprocess.Popen(
            [py(), str(REPO / "scripts" / "mutate.py"), "--parent", piece],
            stdout=open(log_path, "w"), stderr=subprocess.STDOUT,
            cwd=str(REPO),
        )
        piece_pids[piece] = p.pid
        procs.append((piece, p))
        log(f"  ▶ mutate {piece}  PID {p.pid}  → .logs/{log_path.name}")
        time.sleep(0.5)

    update_pids(piece_pids)

    # Critic slot — evaluates recent output + queues improvement directives
    critic_proc = None
    if args.critic:
        log_path = LOG_DIR / f"critic-{datetime.now(timezone.utc).strftime('%H%M%S')}.log"
        critic_proc = subprocess.Popen(
            [py(), str(REPO / "scripts" / "evaluator.py")],
            stdout=open(log_path, "w"), stderr=subprocess.STDOUT,
            cwd=str(REPO),
        )
        log(f"  ▶ critic/evaluator  PID {critic_proc.pid}  → .logs/{log_path.name}")

    log(f"Waiting for {len(pieces)} worker(s)…")
    done: set[str] = set()
    start_time = time.time()
    completion_times: list[float] = []
    last_progress_log = start_time

    while len(done) < len(procs):
        for piece, p in procs:
            if piece not in done and p.poll() is not None:
                rc = p.returncode
                elapsed = time.time() - start_time
                completion_times.append(elapsed)
                log(f"  {'✓' if rc == 0 else '✗'} {piece} done in {elapsed:.0f}s (rc={rc})")
                done.add(piece)

        # Progress update every 30s
        now = time.time()
        if now - last_progress_log >= 30 and len(done) > 0 and len(done) < len(procs):
            elapsed = now - start_time
            avg_per_piece = elapsed / len(done) if done else 0
            remaining = len(procs) - len(done)

            # Count how many pieces in queue are not yet started
            taste = json.loads(TASTE.read_text())
            all_in_queue = list(taste.get("iterate_when_chosen", {}).keys())
            claimed = _clean_stale(_load_claims())
            yet_to_start = len([p for p in all_in_queue if p not in claimed])

            if avg_per_piece > 0:
                eta_sec = remaining * avg_per_piece
                eta_min = int(eta_sec // 60)
                log(f"  {len(done)}/{len(procs)} done · {remaining} in-flight · ~{eta_min}m remaining · {yet_to_start} pieces yet to queue")

            last_progress_log = now

        time.sleep(5)

    if critic_proc:
        critic_proc.wait()
        log(f"  ✓ critic done (rc={critic_proc.returncode})")

    release(pieces)
    log(f"Batch complete — released: {' '.join(pieces)}")

    notify_telegram(pieces)


if __name__ == "__main__":
    main()
