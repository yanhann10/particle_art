"""Spend guard: enforces hard daily/monthly caps on Bedrock-paid mutations.

State file: ~/.particle_art_budget.json (lives outside the repo so push history doesn't track spend).
Subscription calls cost $0 to this counter — only paid Bedrock invocations are tallied.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

STATE = Path(os.environ.get("PARTICLE_ART_BUDGET",
                            os.path.expanduser("~/.particle_art_budget.json")))

# defaults; override via env
DAILY_USD_CAP   = float(os.environ.get("PARTICLE_ART_DAILY_CAP",   "0.60"))
MONTHLY_USD_CAP = float(os.environ.get("PARTICLE_ART_MONTHLY_CAP", "15.00"))
DAILY_RUN_CAP   = int(os.environ.get("PARTICLE_ART_DAILY_RUNS",    "8"))


def _load() -> dict:
    if not STATE.exists():
        return {"days": {}, "months": {}, "events": []}
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"days": {}, "months": {}, "events": []}


def _save(d: dict):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(d, indent=2))


def _today_key():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _month_key():
    return datetime.now(timezone.utc).strftime("%Y-%m")


def can_spend(estimate_usd: float) -> tuple[bool, str]:
    d = _load()
    day = d["days"].get(_today_key(), {"usd": 0.0, "runs": 0})
    month = d["months"].get(_month_key(), {"usd": 0.0})
    if day["runs"] >= DAILY_RUN_CAP:
        return False, f"daily run cap reached ({DAILY_RUN_CAP})"
    if day["usd"] + estimate_usd > DAILY_USD_CAP:
        return False, f"daily ${DAILY_USD_CAP} cap would be exceeded"
    if month["usd"] + estimate_usd > MONTHLY_USD_CAP:
        return False, f"monthly ${MONTHLY_USD_CAP} cap would be exceeded"
    return True, "ok"


def record(usd: float, provider: str, note: str = ""):
    d = _load()
    day = d["days"].setdefault(_today_key(), {"usd": 0.0, "runs": 0})
    month = d["months"].setdefault(_month_key(), {"usd": 0.0})
    day["usd"] = round(day["usd"] + usd, 4)
    day["runs"] += 1
    month["usd"] = round(month["usd"] + usd, 4)
    d["events"].append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
        "usd": usd,
        "note": note,
    })
    # keep last 500 events
    d["events"] = d["events"][-500:]
    _save(d)


def status() -> dict:
    d = _load()
    day = d["days"].get(_today_key(), {"usd": 0.0, "runs": 0})
    month = d["months"].get(_month_key(), {"usd": 0.0})
    return {
        "today": day,
        "month": month,
        "caps": {
            "daily_usd": DAILY_USD_CAP,
            "monthly_usd": MONTHLY_USD_CAP,
            "daily_runs": DAILY_RUN_CAP,
        },
    }


if __name__ == "__main__":
    print(json.dumps(status(), indent=2))
