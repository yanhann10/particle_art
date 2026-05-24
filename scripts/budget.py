"""Spend guard: enforces hard daily/monthly caps on Bedrock-paid mutations.

State file: ~/.particle_art_budget.json (lives outside the repo so push history doesn't track spend).
Subscription calls cost $0 to this counter — only paid Bedrock invocations are tallied.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

try:
    import urllib.request as _ureq
    import urllib.parse as _uparse
except ImportError:
    _ureq = None

STATE = Path(os.environ.get("PARTICLE_ART_BUDGET",
                            os.path.expanduser("~/.particle_art_budget.json")))

# defaults; override via env
DAILY_USD_CAP   = float(os.environ.get("PARTICLE_ART_DAILY_CAP",   "3.50"))   # was 0.60 — bumped for */30 cadence + swarm debate
MONTHLY_USD_CAP = float(os.environ.get("PARTICLE_ART_MONTHLY_CAP", "60.00"))  # was 15.00
DAILY_RUN_CAP   = int(os.environ.get("PARTICLE_ART_DAILY_RUNS",    "100"))    # was 8 — */30 = 48/day mutate + 48/day debate

ALERT_THRESHOLD = 0.80  # send Telegram alert when spend crosses this fraction of any cap


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


def _send_alert(msg: str):
    """Fire-and-forget Telegram message; silently skips if env vars absent."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat  = os.environ.get("ALLOWED_CHAT_ID", "")
    if not token or not chat or _ureq is None:
        return
    try:
        payload = _uparse.urlencode({"chat_id": chat, "text": msg, "parse_mode": "Markdown"}).encode()
        req = _ureq.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=payload)
        _ureq.urlopen(req, timeout=8)
    except Exception:
        pass


def _check_alerts(d: dict, day_key: str, month_key: str):
    """Send at-most-once alerts when spend crosses ALERT_THRESHOLD of any cap."""
    alerts_sent = d.setdefault("alerts_sent", {})
    day_alerts   = alerts_sent.setdefault(day_key,   [])
    month_alerts = alerts_sent.setdefault(month_key, [])

    day   = d["days"].get(day_key,   {"usd": 0.0, "runs": 0})
    month = d["months"].get(month_key, {"usd": 0.0})

    checks = [
        ("daily_usd_80",   day["usd"]   / DAILY_USD_CAP   >= ALERT_THRESHOLD, day_alerts,
         f"⚠️ *particle-art budget*: daily spend at "
         f"${day['usd']:.2f} / ${DAILY_USD_CAP:.2f} ({day['usd']/DAILY_USD_CAP*100:.0f}%)"),
        ("daily_runs_80",  day["runs"]  / DAILY_RUN_CAP   >= ALERT_THRESHOLD, day_alerts,
         f"⚠️ *particle-art budget*: daily runs at "
         f"{day['runs']} / {DAILY_RUN_CAP} ({day['runs']/DAILY_RUN_CAP*100:.0f}%)"),
        ("monthly_usd_80", month["usd"] / MONTHLY_USD_CAP >= ALERT_THRESHOLD, month_alerts,
         f"⚠️ *particle-art budget*: monthly spend at "
         f"${month['usd']:.2f} / ${MONTHLY_USD_CAP:.2f} ({month['usd']/MONTHLY_USD_CAP*100:.0f}%)"),
    ]

    for key, triggered, sent_list, msg in checks:
        if triggered and key not in sent_list:
            _send_alert(msg)
            sent_list.append(key)


def record(usd: float, provider: str, note: str = ""):
    d = _load()
    day_key   = _today_key()
    month_key = _month_key()
    day   = d["days"].setdefault(day_key,   {"usd": 0.0, "runs": 0})
    month = d["months"].setdefault(month_key, {"usd": 0.0})
    day["usd"]   = round(day["usd"] + usd, 4)
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
    _check_alerts(d, day_key, month_key)
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
