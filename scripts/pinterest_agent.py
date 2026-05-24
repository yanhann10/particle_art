#!/usr/bin/env python3
"""Pinterest inspiration agent.

Reads new pins from a private Pinterest board, analyses each image with
a multimodal LLM, and appends approved directives to pending_directives.jsonl
for the creator agent to pick up on the next tick.

Usage:
    pinterest_agent.py [--board-id BOARD_ID] [--dry-run] [--limit N]

Required env vars (store in Doppler ai-api/dev):
    PINTEREST_ACCESS_TOKEN   long-lived user access token
    TELEGRAM_BOT_TOKEN       for completion notifications
    ALLOWED_CHAT_ID          Telegram chat id

Optional env vars:
    PINTEREST_BOARD_ID       override --board-id flag
    PARTICLE_ART_REPO        path to repo root (default: script parent's parent)
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

REPO         = Path(os.environ.get("PARTICLE_ART_REPO", Path(__file__).resolve().parent.parent))
SEEN         = REPO / "scripts" / "seen_pins.json"
QUEUE        = REPO / "scripts" / "pending_directives.jsonl"
TASTE        = REPO / "taste.json"
FEEDBACK_LOG = REPO / "scripts" / "aesthetic_feedback.jsonl"

PINTEREST_API = "https://api.pinterest.com/v5"

# ── Pinterest API helpers ─────────────────────────────────────────────────────

def _pin_headers() -> dict:
    token = os.environ.get("PINTEREST_ACCESS_TOKEN", "")
    if not token:
        sys.exit("PINTEREST_ACCESS_TOKEN not set — run setup_pinterest_auth.py first")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def list_boards() -> list[dict]:
    url = f"{PINTEREST_API}/boards?page_size=50"
    req = urllib.request.Request(url, headers=_pin_headers())
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["items"]


def list_pins(board_id: str, limit: int = 50) -> list[dict]:
    url = f"{PINTEREST_API}/boards/{board_id}/pins?page_size={limit}"
    req = urllib.request.Request(url, headers=_pin_headers())
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read()).get("items", [])


def download_image_b64(url: str) -> tuple[str, str]:
    """Download image from URL, return (base64_str, media_type)."""
    req = urllib.request.Request(url, headers={"User-Agent": "particle-art-agent/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read()
        ct = r.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
    return base64.b64encode(data).decode(), ct


# ── Seen-pin dedup ────────────────────────────────────────────────────────────

def load_seen() -> set[str]:
    if SEEN.exists():
        return set(json.loads(SEEN.read_text()))
    return set()


def save_seen(seen: set[str]) -> None:
    SEEN.write_text(json.dumps(sorted(seen), indent=2))


# ── Taste gate (inline until evaluator agent #3 is live) ─────────────────────

def _load_taste() -> dict:
    if TASTE.exists():
        try:
            return json.loads(TASTE.read_text())
        except Exception:
            pass
    return {}


def _load_aesthetic_failures(n: int = 20) -> list[str]:
    """Return recent distinct anti-pattern labels from aesthetic_feedback.jsonl."""
    if not FEEDBACK_LOG.exists():
        return []
    lines = FEEDBACK_LOG.read_text().splitlines()[-n:]
    patterns: list[str] = []
    seen: set[str] = set()
    for ln in lines:
        try:
            for p in json.loads(ln).get("anti_patterns_hit", []):
                if p not in seen:
                    patterns.append(p)
                    seen.add(p)
        except Exception:
            pass
    return patterns[:10]


def taste_gate(directive: str) -> tuple[bool, str]:
    """Return (approved, reason). Rejects if directive echoes a disliked direction."""
    taste = _load_taste()
    dislikes = taste.get("dislikes", {}).get("directions", [])
    directive_lower = directive.lower()
    for d in dislikes:
        if any(word in directive_lower for word in d.lower().split()[:3]):
            return False, f"conflicts with disliked direction: {d}"
    return True, "ok"


# ── LLM analysis ─────────────────────────────────────────────────────────────

SYSTEM = (
    "You are an assistant for a generative particle-art system. "
    "The system produces self-contained three.js + GLSL pieces. "
    "Your job is to look at an image the user saved as inspiration and "
    "translate it into concrete mutation directives."
)

USER_TMPL = (
    "The user pinned this image to their art inspiration board on Pinterest.\n"
    "Title/description hint: {desc}\n\n"
    "{failure_block}"
    "Respond with a JSON object only — no prose:\n"
    '{{"directive": "<15-30 word mutation directive for the particle-art creator>", '
    '"rationale": "<1 sentence on what formal/material move you extracted>", '
    '"score": <1-10 how visually inventive this is for a generative-art system>}}'
)

_FAILURE_TMPL = (
    "ANTI-PATTERNS that have recently caused pieces to be rejected — "
    "do NOT generate directives that would produce these:\n"
    "{items}\n\n"
)


def analyse_pin(pin: dict, dry_run: bool, failures: list[str] | None = None) -> dict | None:
    """Download pin image, call vision LLM, return parsed result or None."""
    media = pin.get("media") or {}
    images = media.get("images") or {}
    # prefer 600px, fall back to any available size
    img_url = (
        (images.get("600x") or {}).get("url")
        or next((v.get("url") for v in images.values() if v.get("url")), None)
    )
    if not img_url:
        print(f"  skip {pin['id']}: no image URL")
        return None

    desc = (pin.get("title") or pin.get("description") or "")[:200]
    print(f"  analysing {pin['id']} — {desc[:60]}")

    if dry_run:
        return {"directive": "DRY RUN directive", "rationale": "dry run", "score": 7}

    try:
        b64, media_type = download_image_b64(img_url)
    except Exception as e:
        print(f"  download failed: {e}")
        return None

    failure_block = ""
    if failures:
        failure_block = _FAILURE_TMPL.format(items="\n".join(f"  - {p}" for p in failures))

    from lib_claude import call_bedrock_vision, ProviderError
    try:
        raw = call_bedrock_vision(SYSTEM, USER_TMPL.format(desc=desc, failure_block=failure_block), b64, media_type)
    except ProviderError as e:
        print(f"  vision call failed: {e}")
        return None

    # strip markdown fences if present
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1].lstrip("json").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print(f"  could not parse LLM JSON: {raw[:120]}")
        return None


# ── Queue writer ──────────────────────────────────────────────────────────────

def enqueue(directive: str, pin: dict, rationale: str) -> None:
    entry = {
        "source":    "pinterest",
        "directive": directive,
        "rationale": rationale,
        "pin_id":    pin["id"],
        "pin_url":   f"https://pinterest.com/pin/{pin['id']}/",
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(QUEUE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"  ✓ queued: {directive[:70]}")


# ── Telegram notify ───────────────────────────────────────────────────────────

def notify(msg: str) -> None:
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("ALLOWED_CHAT_ID", "")
    if not token or not chat_id:
        return
    data = urllib.parse.urlencode({
        "chat_id": chat_id, "text": msg, "parse_mode": "Markdown",
    }).encode()
    try:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data, timeout=10,
        )
    except Exception:
        pass


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Pinterest inspiration agent")
    ap.add_argument("--board-id", default=os.environ.get("PINTEREST_BOARD_ID", ""))
    ap.add_argument("--dry-run",  action="store_true")
    ap.add_argument("--limit",    type=int, default=50)
    ap.add_argument("--list-boards", action="store_true", help="list boards and exit")
    args = ap.parse_args()

    if args.list_boards:
        for b in list_boards():
            priv = "🔒" if b.get("privacy") == "SECRET" else "🌐"
            print(f"{priv} {b['id']}  {b['name']}")
        return

    if not args.board_id:
        sys.exit("--board-id required (or set PINTEREST_BOARD_ID). "
                 "Run with --list-boards to find your board ID.")

    seen = load_seen()
    pins = list_pins(args.board_id, limit=args.limit)
    new_pins = [p for p in pins if p["id"] not in seen]
    print(f"Pinterest agent: {len(pins)} pins, {len(new_pins)} new")

    failures = _load_aesthetic_failures()
    if failures:
        print(f"aesthetic feedback: {len(failures)} recent anti-pattern(s) loaded as negative context")

    queued, skipped = 0, 0
    for pin in new_pins:
        result = analyse_pin(pin, args.dry_run, failures=failures)
        seen.add(pin["id"])

        if not result:
            skipped += 1
            continue

        directive = result.get("directive", "")
        rationale = result.get("rationale", "")
        score     = result.get("score", 0)

        if score < 6:
            print(f"  skip low-score ({score}/10): {directive[:50]}")
            skipped += 1
            continue

        approved, reason = taste_gate(directive)
        if not approved:
            print(f"  taste-gate rejected: {reason}")
            skipped += 1
            continue

        if not args.dry_run:
            enqueue(directive, pin, rationale)
        queued += 1
        time.sleep(0.5)  # be polite to Pinterest API

    if not args.dry_run:
        save_seen(seen)

    summary = (
        f"📌 *Pinterest agent*: {len(new_pins)} new pins → "
        f"{queued} directives queued, {skipped} skipped"
    )
    print(summary)
    if queued > 0 and not args.dry_run:
        notify(summary)


if __name__ == "__main__":
    main()
