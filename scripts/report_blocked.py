#!/usr/bin/env python3
"""Blocked-task reporter for particle_art agents.

Agents call report_blocked() when stuck; humans run this script to review.

Usage:
    python3 scripts/report_blocked.py                        # show all unresolved
    python3 scripts/report_blocked.py --resolve "curator.py" # mark resolved
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
BLOCKED_FILE = SCRIPTS_DIR / "blocked_tasks.jsonl"


def report_blocked(agent: str, reason: str, needs: str, notify: bool = True) -> None:
    """Call from any agent when it cannot proceed autonomously.

    agent:  script name (e.g. "curator.py")
    reason: why it's blocked (e.g. "ARTFORUM_KEY not set")
    needs:  what human action unblocks it (e.g. "set ARTFORUM_KEY in env")
    """
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "reason": reason,
        "needs": needs,
        "resolved": False,
    }
    with open(BLOCKED_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")

    if notify:
        try:
            import importlib.util, subprocess
            notify_path = SCRIPTS_DIR / "notify_telegram.py"
            if notify_path.exists():
                msg = f"\U0001f6a7 {agent} blocked: {reason}. Needs: {needs}"
                subprocess.run(
                    [sys.executable, str(notify_path)],
                    input=msg, capture_output=True, text=True, timeout=15,
                    env={**__import__("os").environ, "_BLOCKED_MSG": msg},
                )
                # simpler: use requests directly if env vars are set
                _telegram_text(msg)
        except Exception:
            pass


def _telegram_text(text: str) -> None:
    import os
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("ALLOWED_CHAT_ID")
    if not (token and chat_id):
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception:
        pass


def _load_tasks() -> list[dict]:
    if not BLOCKED_FILE.exists():
        return []
    tasks = []
    for line in BLOCKED_FILE.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                tasks.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return tasks


def _save_tasks(tasks: list[dict]) -> None:
    with open(BLOCKED_FILE, "w") as f:
        for t in tasks:
            f.write(json.dumps(t) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resolve", metavar="AGENT", help="mark all blocks for AGENT as resolved")
    args = parser.parse_args()

    tasks = _load_tasks()

    if args.resolve:
        count = 0
        for t in tasks:
            if t["agent"] == args.resolve and not t["resolved"]:
                t["resolved"] = True
                count += 1
        _save_tasks(tasks)
        print(f"Resolved {count} block(s) for {args.resolve}")
        return

    unresolved = [t for t in tasks if not t.get("resolved")]
    if not unresolved:
        print("No unresolved blocked tasks.")
        return
    print(f"{'TS':<28}  {'AGENT':<20}  {'REASON':<40}  NEEDS")
    print("-" * 110)
    for t in unresolved:
        print(f"{t['ts']:<28}  {t['agent']:<20}  {t['reason']:<40}  {t['needs']}")


if __name__ == "__main__":
    main()
