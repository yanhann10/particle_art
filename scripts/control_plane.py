#!/usr/bin/env python3
"""
control_plane.py  —  particle_art project at-a-glance dashboard.

Usage:
    python3 scripts/control_plane.py          # pretty terminal output
    python3 scripts/control_plane.py --json   # machine-readable JSON

Requires: gh CLI (authenticated), Python 3.8+.
Works from any directory inside the repo.
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


# ── helpers ───────────────────────────────────────────────────────────────────

def run(cmd: str) -> tuple[str, bool]:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return r.stdout.strip(), r.returncode == 0


def gh(endpoint: str, jq: str = "") -> list | dict | None:
    jq_part = f" --jq '{jq}'" if jq else ""
    out, ok = run(f"gh api {endpoint}{jq_part}")
    if not ok:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return out.splitlines() if out else None


# ── data collectors ───────────────────────────────────────────────────────────

def collect_issues() -> list[dict]:
    out, ok = run(
        "gh issue list --state all --limit 100 "
        "--json number,title,state,labels,assignees"
    )
    return json.loads(out) if ok else []


def collect_branches() -> set[str]:
    out, ok = run("gh api repos/:owner/:repo/git/refs/heads --jq '.[].ref'")
    if not ok:
        return set()
    return {b.replace("refs/heads/", "") for b in out.splitlines() if b.strip()}


def collect_prs() -> list[dict]:
    out, ok = run(
        "gh pr list --state all --limit 50 "
        "--json number,title,headRefName,state"
    )
    return json.loads(out) if ok else []


def collect_system_state() -> dict:
    pieces_dir = REPO / "pieces"
    pieces = sum(
        1 for p in pieces_dir.iterdir()
        if p.is_dir() and (p / "index.html").exists()
    ) if pieces_dir.exists() else 0

    queue_file = REPO / "scripts" / "pending_directives.jsonl"
    queue = sum(1 for l in queue_file.open() if l.strip()) if queue_file.exists() else 0

    log_file = REPO / "scripts" / "mutation_log.jsonl"
    last_tick = None
    if log_file.exists():
        lines = [l for l in log_file.open() if l.strip()]
        if lines:
            try:
                entry = json.loads(lines[-1])
                last_tick = entry.get("ts") or entry.get("timestamp") or entry.get("created_at")
            except (json.JSONDecodeError, KeyError):
                pass

    return {"pieces": pieces, "queue_depth": queue, "last_tick": last_tick}


VM_SSH = "ubuntu@ec2-13-223-233-226.compute-1.amazonaws.com"
VM_KEY = Path.home() / "Downloads/cloud/aws_microvm.pem"


def _vm_crontab() -> str:
    """Return VM crontab text, or '' on failure."""
    if not VM_KEY.exists():
        return ""
    out, ok = run(
        f'ssh -i {VM_KEY} -o ConnectTimeout=6 -o StrictHostKeyChecking=no '
        f'{VM_SSH} "crontab -l 2>/dev/null"'
    )
    return out if ok else ""


def collect_agents() -> list[dict]:
    scripts_dir = REPO / "scripts"
    candidates = sorted(
        p.name for p in scripts_dir.iterdir()
        if p.suffix in (".py", ".sh") and p.stat().st_size > 0
        and any(kw in p.name for kw in ("tick", "cron", "inbox", "drain", "scout", "agent", "bot"))
    )

    # Scheduling lives on the VM, not local
    vm_cron = _vm_crontab()
    vm_ok = bool(vm_cron)

    result = []
    for name in candidates:
        sched_line = next(
            (l for l in vm_cron.splitlines() if name in l and not l.startswith("#")), None
        )
        # Extract just the time spec (first 5 fields)
        schedule = ""
        if sched_line:
            parts = sched_line.split()
            if parts[0].startswith("@"):
                schedule = parts[0]
            elif len(parts) >= 5:
                schedule = " ".join(parts[:5])
        result.append({
            "script": name,
            "scheduled": bool(sched_line),
            "schedule": schedule,
            "vm_ok": vm_ok,
        })
    return result


# ── rendering ─────────────────────────────────────────────────────────────────

def fmt_age(ts_str: str | None) -> str:
    if not ts_str:
        return "—"
    try:
        from datetime import timezone
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - ts
        h = int(delta.total_seconds() // 3600)
        if h < 1:
            return f"{int(delta.total_seconds()//60)}m ago"
        if h < 24:
            return f"{h}h ago"
        return f"{h//24}d ago"
    except Exception:
        return ts_str[:16]


def print_report(issues, branches, prs, state, agents):
    W = 72
    pr_by_branch = {pr["headRefName"]: pr for pr in prs}

    def bar():
        print("─" * W)

    bar()
    print(f"  PARTICLE ART  ·  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    bar()

    # ── System state ──────────────────────────────────────────────────────────
    tick_age = fmt_age(state["last_tick"])
    print(f"\n  SYSTEM   pieces:{state['pieces']}   queue:{state['queue_depth']}   last-tick:{tick_age}")

    # ── Issues ────────────────────────────────────────────────────────────────
    open_issues = [i for i in issues if i["state"] == "OPEN"]
    closed_count = sum(1 for i in issues if i["state"] == "CLOSED")

    grouped: dict[str, list] = {}
    for issue in open_issues:
        label = issue["labels"][0]["name"] if issue["labels"] else "unlabeled"
        grouped.setdefault(label, []).append(issue)

    print(f"\n  ISSUES   {len(open_issues)} open   {closed_count} closed\n")
    for label in sorted(grouped):
        print(f"  [{label}]")
        for iss in sorted(grouped[label], key=lambda x: x["number"]):
            n = iss["number"]
            title = iss["title"][:58]
            feat = next((b for b in branches if b.startswith(f"feat/{n}-")), None)
            pr = pr_by_branch.get(feat) if feat else None

            if pr and pr["state"] == "MERGED":
                icon, note = "●", f"merged PR#{pr['number']}"
            elif pr:
                icon, note = "◑", f"PR#{pr['number']} {pr['state'].lower()}"
            elif feat:
                icon, note = "◐", feat
            else:
                icon, note = "○", ""

            print(f"    {icon} #{n:<3}  {title}")
            if note:
                print(f"           └─ {note}")
        print()

    # ── Agents ────────────────────────────────────────────────────────────────
    if agents:
        vm_status = "VM" if agents[0].get("vm_ok") else "VM unreachable"
        print(f"  AGENTS  [{vm_status}]")
        for a in agents:
            if a["scheduled"]:
                print(f"    ✓  {a['script']:<32}  {a['schedule']}")
            else:
                print(f"    ○  {a['script']}")
        print()

    bar()


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    as_json = "--json" in sys.argv

    issues  = collect_issues()
    branches = collect_branches()
    prs     = collect_prs()
    state   = collect_system_state()
    agents  = collect_agents()

    if as_json:
        print(json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "system": state,
            "issues": {"open": [i for i in issues if i["state"] == "OPEN"],
                       "closed_count": sum(1 for i in issues if i["state"] == "CLOSED")},
            "agents": agents,
        }, indent=2))
    else:
        print_report(issues, branches, prs, state, agents)


if __name__ == "__main__":
    main()
