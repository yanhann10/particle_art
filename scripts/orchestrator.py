#!/usr/bin/env python3
"""orchestrator.py — single entry point for all cron-driven agents.

Reads scripts/agents.json, checks trigger conditions + cooldowns, then
spawns eligible agents as subprocesses with per-agent log capture.

Single crontab line replaces all per-agent crons:
  */12 * * * *  /path/to/particle_art/scripts/orchestrator.py

Usage:
    python3 scripts/orchestrator.py                     # normal run
    python3 scripts/orchestrator.py --dry-run           # print plan, no spawn
    python3 scripts/orchestrator.py --list              # show manifest + last run
    python3 scripts/orchestrator.py --agent improv      # run only one agent
    python3 scripts/orchestrator.py --dry-run --agent improv

Python 3.8+, stdlib only.
"""
import argparse
import fcntl
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone, date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
LOGS_DIR = REPO / ".logs"
MANIFEST = SCRIPTS / "agents.json"
STATE_FILE = LOGS_DIR / "orchestrator_state.json"
LOCK_FILE = REPO / ".orchestrator.lock"

# pending_directives.jsonl used by queue_nonempty trigger
PENDING_DIRECTIVES = SCRIPTS / "pending_directives.jsonl"
# lineage.json used by on_new_piece trigger
LINEAGE = REPO / "lineage.json"

SUBPROCESS_TIMEOUT = 300  # seconds
VENV_PYTHON = REPO / ".venv" / "bin" / "python3"


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logging() -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"orchestrator-{datetime.now(timezone.utc).strftime('%Y%m')}.log"
    fmt = "[%(asctime)s] %(levelname)s %(message)s"
    datefmt = "%Y-%m-%dT%H:%M:%SZ"

    logger = logging.getLogger("orchestrator")
    logger.setLevel(logging.DEBUG)

    # File handler
    fh = logging.FileHandler(log_path)
    fh.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    fh.setLevel(logging.DEBUG)
    logger.addHandler(fh)

    # Console handler (INFO+)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    ch.setLevel(logging.INFO)
    logger.addHandler(ch)

    return logger


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def load_state() -> dict:
    """Load agent run state from .logs/orchestrator_state.json."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_state(state: dict) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Trigger evaluation
# ---------------------------------------------------------------------------

def _pieces_count() -> int:
    """Count pieces in pieces/ by counting meta.json files."""
    pieces_dir = REPO / "pieces"
    if not pieces_dir.exists():
        return 0
    return sum(1 for _ in pieces_dir.rglob("meta.json"))


def _queue_nonempty() -> bool:
    """True if pending_directives.jsonl has at least one line."""
    if not PENDING_DIRECTIVES.exists():
        return False
    try:
        text = PENDING_DIRECTIVES.read_text().strip()
        return bool(text)
    except Exception:
        return False


def _is_eligible(agent: dict, state: dict, now: datetime, logger: logging.Logger) -> tuple[bool, str]:
    """Return (eligible, reason_string)."""
    name = agent["name"]
    trigger = agent.get("trigger", "always")
    cooldown_min = agent.get("cooldown_min", 0)
    agent_state = state.get(name, {})
    last_run_str = agent_state.get("last_run")

    # --- Cooldown check (applies to all trigger types) ---
    if last_run_str and cooldown_min > 0:
        try:
            last_run = datetime.fromisoformat(last_run_str)
            elapsed = (now - last_run).total_seconds() / 60.0
            if elapsed < cooldown_min:
                remaining = cooldown_min - elapsed
                return False, f"cooldown ({remaining:.0f}min remaining)"
        except ValueError:
            pass  # malformed timestamp — treat as never run

    # --- Trigger-specific checks ---
    if trigger == "always":
        return True, "always"

    if trigger == "queue_nonempty":
        if _queue_nonempty():
            return True, "queue_nonempty: directives pending"
        return False, "queue_nonempty: queue empty"

    if trigger == "on_new_piece":
        pieces_now = _pieces_count()
        stored_count = state.get("_pieces_count", 0)
        if pieces_now > stored_count:
            return True, f"on_new_piece: {stored_count} → {pieces_now}"
        return False, f"on_new_piece: no new pieces (count={pieces_now})"

    if trigger.startswith("schedule:"):
        # format: schedule:HH:MM  (UTC)
        try:
            hh, mm = trigger[len("schedule:"):].split(":")
            scheduled_hour = int(hh)
            scheduled_min = int(mm)
        except ValueError:
            return False, f"bad schedule format: {trigger!r}"

        # Fire if current UTC time >= scheduled time AND hasn't run today
        today_utc = now.date()
        last_run_date: date | None = None
        if last_run_str:
            try:
                last_run_date = datetime.fromisoformat(last_run_str).date()
            except ValueError:
                pass

        current_minutes = now.hour * 60 + now.minute
        scheduled_minutes = scheduled_hour * 60 + scheduled_min
        if current_minutes < scheduled_minutes:
            return False, (
                f"schedule:{hh}:{mm} — not yet (current UTC {now.strftime('%H:%M')})"
            )
        if last_run_date == today_utc:
            return False, f"schedule:{hh}:{mm} — already ran today"
        return True, f"schedule:{hh}:{mm} — due (UTC {now.strftime('%H:%M')})"

    return False, f"unknown trigger type: {trigger!r}"


# ---------------------------------------------------------------------------
# Subprocess dispatch
# ---------------------------------------------------------------------------

def _agent_log_path(name: str) -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    month = datetime.now(timezone.utc).strftime("%Y%m")
    return LOGS_DIR / f"{name}-{month}.log"


def _python() -> str:
    """Return path to venv python3 if it exists, else fall back to sys.executable."""
    if VENV_PYTHON.exists():
        return str(VENV_PYTHON)
    return sys.executable


def dispatch_agent(agent: dict, logger: logging.Logger) -> int:
    """Spawn agent as a subprocess, capture output to its monthly log.

    Returns the agent's exit code (or -1 on timeout/exception).
    """
    name = agent["name"]
    script = SCRIPTS / agent["script"]
    args = agent.get("args") or []
    cmd = [_python(), str(script)] + list(args)
    log_path = _agent_log_path(name)

    logger.info("dispatch %s: %s", name, " ".join(cmd))

    try:
        with open(log_path, "a") as log_fh:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            log_fh.write(f"\n── orchestrator dispatch {ts} ──\n")
            log_fh.flush()

            proc = subprocess.Popen(
                cmd,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                cwd=REPO,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            try:
                proc.wait(timeout=SUBPROCESS_TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                logger.warning("agent %s timed out after %ds — killed", name, SUBPROCESS_TIMEOUT)
                log_fh.write(f"[orchestrator] TIMEOUT after {SUBPROCESS_TIMEOUT}s\n")
                return -1

            rc = proc.returncode
            log_fh.write(f"[orchestrator] exit code {rc}\n")

    except Exception as exc:
        logger.error("agent %s failed to launch: %s", name, exc)
        return -1

    if rc != 0:
        logger.warning("agent %s exited rc=%d (see %s)", name, rc, log_path)
    else:
        logger.info("agent %s OK", name)

    return rc


# ---------------------------------------------------------------------------
# Main orchestration loop
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace, logger: logging.Logger) -> int:
    state = load_state()
    now = datetime.now(timezone.utc)

    try:
        manifest: list[dict] = json.loads(MANIFEST.read_text())
    except Exception as exc:
        logger.error("cannot read agents.json: %s", exc)
        return 1

    # Filter to single agent if --agent specified
    if args.agent:
        manifest = [a for a in manifest if a["name"] == args.agent]
        if not manifest:
            logger.error("no agent named %r in agents.json", args.agent)
            return 1

    # --list mode
    if args.list:
        _print_list(manifest, state, now, logger)
        return 0

    # Snapshot pieces count before dispatch for on_new_piece trigger
    pieces_before = _pieces_count()

    any_dispatched = False
    for agent in manifest:
        if not agent.get("enabled", True):
            logger.debug("skip %s (disabled)", agent["name"])
            continue

        eligible, reason = _is_eligible(agent, state, now, logger)
        name = agent["name"]

        if not eligible:
            logger.debug("skip %s — %s", name, reason)
            if args.dry_run:
                print(f"  SKIP  {name:<22} {reason}")
            continue

        if args.dry_run:
            print(f"  WOULD DISPATCH  {name:<22} trigger={agent.get('trigger')} ({reason})")
            continue

        # Real dispatch
        any_dispatched = True
        rc = dispatch_agent(agent, logger)

        # Record state regardless of exit code
        agent_state = state.setdefault(name, {})
        agent_state["last_run"] = now.isoformat()
        agent_state["last_exit"] = rc
        save_state(state)

    # Update pieces_count snapshot after all agents run (on_new_piece bookkeeping)
    if not args.dry_run:
        state["_pieces_count"] = _pieces_count()
        save_state(state)

    if args.dry_run:
        print("(dry-run complete — nothing was executed)")

    return 0


def _print_list(manifest: list[dict], state: dict, now: datetime, logger: logging.Logger) -> None:
    """Print a table of agents with their last-run info and next-eligible status."""
    header = f"{'NAME':<22} {'TRIGGER':<22} {'COOLDOWN':>10} {'LAST RUN':<26} {'STATUS'}"
    print(header)
    print("-" * len(header))
    for agent in manifest:
        name = agent["name"]
        trigger = agent.get("trigger", "always")
        cooldown = agent.get("cooldown_min", 0)
        agent_state = state.get(name, {})
        last_run = agent_state.get("last_run", "never")
        last_exit = agent_state.get("last_exit")
        enabled = agent.get("enabled", True)

        if not enabled:
            status = "DISABLED"
        else:
            eligible, reason = _is_eligible(agent, state, now, logger)
            if eligible:
                status = f"ELIGIBLE ({reason})"
            else:
                status = f"SKIP: {reason}"

        exit_tag = f" [exit={last_exit}]" if last_exit is not None and last_exit != 0 else ""
        print(f"{name:<22} {trigger:<22} {cooldown:>9}m {last_run:<26} {status}{exit_tag}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Orchestrator: dispatch registered agents based on trigger + cooldown."
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="Print dispatch plan without executing anything.")
    ap.add_argument("--agent", metavar="NAME",
                    help="Run (or show plan for) a single named agent.")
    ap.add_argument("--list", action="store_true",
                    help="Show manifest with last-run and next-eligible info, then exit.")
    args = ap.parse_args()

    logger = _setup_logging()

    # flock: prevent overlapping orchestrator invocations
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        lock_fd = open(LOCK_FILE, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        # Another orchestrator is running — skip silently (same pattern as
        # parallel_tick.sh / improv_cron.sh)
        # We still want --list and --dry-run to work without the lock.
        if not (args.list or args.dry_run):
            # Write to stderr so cron captures it without noisy stdout
            print("orchestrator: another instance running, skip", file=sys.stderr)
            return 0
        # For read-only modes, proceed without exclusive lock
        lock_fd = None

    try:
        if not args.dry_run and not args.list:
            logger.info("── orchestrator start ──")
        rc = run(args, logger)
        if not args.dry_run and not args.list:
            logger.info("── orchestrator end (rc=%d) ──", rc)
        return rc
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                lock_fd.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
