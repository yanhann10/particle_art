#!/usr/bin/env python3
"""Inspiration absorber — Telegram direction → pending_directives.jsonl via taste-gate.

Usage (standalone — polls Telegram getUpdates):
    python3 scripts/absorber.py [--dry-run]

Usage (process a single direction string):
    python3 scripts/absorber.py --direction "make it look like roots from a seed"

Usage (called as module from thrum_inbox.py):
    from absorber import absorb_direction
    absorb_direction("make it look like roots from a seed", chat_id=chat_id)

Pipeline for each direction:
  1. Translate free-text → 1–3 concrete mutation directives (LLM)
  2. Taste-gate: LLM scores proposed directives against taste.json (≥6/10 to proceed)
  3. If rejected: send Telegram reply with reason + 2 alternatives; loop up to 3 rounds
  4. If approved: append to pending_directives.jsonl with source: "absorber"
  5. Send Telegram confirmation: "direction queued: ... — next piece in ~12min"

State: last Telegram update_id in ~/.particle_art_absorber_state.json
"""

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import lib_claude

TASTE = REPO / "taste.json"
QUEUE = REPO / "scripts" / "pending_directives.jsonl"
STATE = Path.home() / ".particle_art_absorber_state.json"

MAX_ROUNDS = 3
TASTE_THRESHOLD = 6
DIRECTION_PREFIXES = ("direction:", "new:")


# ── Telegram helpers ──────────────────────────────────────────────────────────

def _tg_api(method: str, data: dict | None = None) -> dict:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return {}
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = json.dumps(data or {}).encode() if data else b""
    req = urllib.request.Request(
        url,
        data=body or None,
        headers={"Content-Type": "application/json"} if body else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[absorber] telegram API error: {e}", file=sys.stderr)
        return {}


def _send_telegram(chat_id: str | int, text: str) -> None:
    _tg_api("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    })


def _get_updates(offset: int | None = None) -> list[dict]:
    params: dict = {"timeout": 0, "allowed_updates": ["message"]}
    if offset is not None:
        params["offset"] = offset
    result = _tg_api("getUpdates", params)
    return result.get("result", [])


# ── State ─────────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            pass
    return {}


def _save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=2))


# ── LLM calls ─────────────────────────────────────────────────────────────────

def _translate_direction(direction: str, taste: dict) -> list[str]:
    """Translate free-text direction → 1–3 concrete mutation directives."""
    likes_text = json.dumps(taste.get("likes", {}), indent=2)
    dislikes_text = json.dumps(taste.get("dislikes", {}), indent=2)

    system = (
        "You are a creative director for a generative particle art system built with three.js.\n"
        "Translate a user's free-text creative direction into 1–3 concrete, actionable mutation "
        "directives. Each directive should specify visual form + technique + palette if relevant.\n\n"
        "Compatible techniques: differential growth, GPGPU ping-pong for attractors, "
        "MeshSurfaceSampler + GLTFLoader for object-clouds, CatmullRomCurve3 + TubeGeometry, "
        "InstancedMesh, L-system, audio-reactive GLSL, two-pose lerp morphs.\n\n"
        f"User taste — LIKES:\n{likes_text}\n\n"
        f"User taste — DISLIKES:\n{dislikes_text}\n\n"
        "Output ONLY a valid JSON array of 1–3 directive strings. No prose. Example:\n"
        '["Differential growth with vascular branching, warm amber particles on dark ground", '
        '"Audio-reactive pulse expanding from center with restrained cream-on-ink palette"]'
    )
    user = f"Translate this creative direction:\n\n{direction}"

    try:
        response, provider = lib_claude.call(system, user)
        print(f"[absorber] translate via {provider}")
        m = re.search(r"\[.*?\]", response, re.DOTALL)
        if m:
            directives = json.loads(m.group(0))
            if isinstance(directives, list) and directives:
                return [str(d).strip() for d in directives[:3] if d]
    except Exception as e:
        print(f"[absorber] translate error: {e}", file=sys.stderr)

    # Fallback: pass through verbatim
    return [direction[:300]]


def _taste_gate(directives: list[str], taste: dict) -> tuple[int, str, list[str]]:
    """Score proposed directives 0–10 against taste.json.

    Returns (score, reason, alternatives).
    Score ≥ TASTE_THRESHOLD = approve. Alternatives are 2 suggestions for the rejection case.
    """
    likes_text = json.dumps(taste.get("likes", {}), indent=2)
    dislikes_text = json.dumps(taste.get("dislikes", {}), indent=2)
    directives_text = "\n".join(f"- {d}" for d in directives)

    system = (
        "You are a taste evaluator for a generative particle art system. "
        "Score proposed mutation directives 0–10 against the user's taste profile.\n\n"
        f"LIKES (scores high):\n{likes_text}\n\n"
        f"DISLIKES (scores low / causes rejection):\n{dislikes_text}\n\n"
        "Scoring rubric:\n"
        "  10: matches ≥2 liked directions + a preferred technique\n"
        "   7: matches ≥1 liked direction, no dislikes triggered\n"
        "   5: neutral or ambiguous — no clear like/dislike match\n"
        "   3: triggers a dislike (noise blob, tiny subject, chaotic palette)\n"
        "   0: directly copies a named disliked piece\n\n"
        "Output ONLY valid JSON:\n"
        '{"score": 7, "reason": "one sentence", '
        '"alternatives": ["alt directive 1 addressing the weakness", "alt directive 2"]}\n\n'
        "If approving (score ≥6), alternatives can be empty [].\n"
        "If rejecting, provide exactly 2 alternatives that fix the weakness."
    )
    user = f"Score these proposed directives:\n{directives_text}"

    try:
        response, provider = lib_claude.call(system, user)
        print(f"[absorber] taste-gate via {provider}")
        m = re.search(r"\{.*?\}", response, re.DOTALL)
        if m:
            result = json.loads(m.group(0))
            score = max(0, min(10, int(result.get("score", 0))))
            reason = str(result.get("reason", "no reason")).strip()
            alternatives = [str(a).strip() for a in result.get("alternatives", [])[:2]]
            return score, reason, alternatives
    except Exception as e:
        print(f"[absorber] taste-gate error: {e}", file=sys.stderr)

    # Fallback: approve at threshold
    return TASTE_THRESHOLD, "evaluation failed (fallback approve)", []


# ── Core absorber logic ───────────────────────────────────────────────────────

def absorb_direction(
    direction: str,
    chat_id: str | int | None = None,
    dry_run: bool = False,
) -> bool:
    """Process a creative direction through translate → taste-gate → queue.

    Returns True if a directive was queued, False if rejected after max rounds.
    """
    taste: dict = {}
    if TASTE.exists():
        try:
            taste = json.loads(TASTE.read_text())
        except Exception:
            pass

    print(f"[absorber] direction: {direction[:100]}")

    current_direction = direction
    last_alternatives: list[str] = []

    for round_num in range(1, MAX_ROUNDS + 1):
        print(f"[absorber] round {round_num}/{MAX_ROUNDS}")

        # Translate → concrete directives
        directives = _translate_direction(current_direction, taste)
        print(f"[absorber] proposed: {directives}")

        # Taste gate
        score, reason, alternatives = _taste_gate(directives, taste)
        print(f"[absorber] score {score}/10: {reason}")
        last_alternatives = alternatives

        if score >= TASTE_THRESHOLD:
            # ── Approved ──────────────────────────────────────────────────────
            if not dry_run:
                now = datetime.now(timezone.utc).isoformat()
                for directive in directives:
                    entry = {
                        "source": "absorber",
                        "directive": directive,
                        "original_direction": direction,
                        "score": score,
                        "queued_at": now,
                    }
                    with QUEUE.open("a") as f:
                        f.write(json.dumps(entry) + "\n")

            summary = directives[0][:120]
            if len(directives) > 1:
                summary += f" (+{len(directives)-1} more)"

            msg = (
                f"✅ *Direction queued* (score {score}/10)\n"
                f"_{summary}_\n"
                f"Next piece in ~12min"
            )
            print(f"[absorber] approved → queued")
            if chat_id and not dry_run:
                _send_telegram(chat_id, msg)
            return True

        # ── Rejected ──────────────────────────────────────────────────────────
        if round_num == MAX_ROUNDS:
            alt_lines = ""
            if last_alternatives:
                alt_lines = "\n\nTry instead:\n" + "\n".join(
                    f"• {a}" for a in last_alternatives
                )
            msg = (
                f"❌ *Direction rejected after {MAX_ROUNDS} rounds* "
                f"(final score {score}/10)\n"
                f"_{reason}_{alt_lines}"
            )
            print(f"[absorber] exhausted rounds — rejected")
            if chat_id and not dry_run:
                _send_telegram(chat_id, msg)
            return False

        # Still have rounds — send feedback and retry with first alternative
        alt_lines = ""
        if alternatives:
            alt_lines = "\n\nRefining with:\n" + "\n".join(
                f"• {a}" for a in alternatives
            )
        msg = (
            f"⚠️ *Round {round_num}/{MAX_ROUNDS}* — score {score}/10\n"
            f"_{reason}_{alt_lines}"
        )
        if chat_id and not dry_run:
            _send_telegram(chat_id, msg)

        if alternatives:
            current_direction = alternatives[0]
            print(f"[absorber] retrying with: {current_direction[:80]}")
        else:
            # No alternatives to try — give up early
            msg = (
                f"❌ *Direction rejected* (score {score}/10)\n"
                f"_{reason}_\n"
                f"No alternatives generated. Try rephrasing."
            )
            if chat_id and not dry_run:
                _send_telegram(chat_id, msg)
            return False

    return False


# ── Telegram poll ─────────────────────────────────────────────────────────────

def _extract_direction(text: str) -> str | None:
    """Return direction payload if message starts with a direction prefix, else None."""
    stripped = text.strip()
    low = stripped.lower()
    for prefix in DIRECTION_PREFIXES:
        if low.startswith(prefix):
            payload = stripped[len(prefix):].strip()
            return payload if payload else None
    return None


def poll_telegram(dry_run: bool = False) -> int:
    """Poll Telegram getUpdates; process direction: / new: messages.

    Returns count of directions processed.
    """
    allowed_chat = os.environ.get("ALLOWED_CHAT_ID", "")
    state = _load_state()
    offset: int | None = state.get("telegram_offset")

    updates = _get_updates(offset)
    if not updates:
        print("[absorber] no telegram updates")
        return 0

    processed = 0
    max_update_id = offset or 0

    for update in updates:
        update_id = update.get("update_id", 0)
        if update_id >= max_update_id:
            max_update_id = update_id

        msg = update.get("message", {})
        text = msg.get("text", "")
        chat_id = msg.get("chat", {}).get("id")

        # Only process from the allowed chat
        if allowed_chat and str(chat_id) != str(allowed_chat):
            continue

        direction = _extract_direction(text)
        if direction:
            print(f"[absorber] telegram direction: {direction[:80]}")
            absorb_direction(direction, chat_id=chat_id, dry_run=dry_run)
            processed += 1

    # Advance offset past all seen updates
    state["telegram_offset"] = max_update_id + 1
    _save_state(state)

    return processed


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inspiration absorber")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Translate + gate but don't write to queue or send Telegram",
    )
    parser.add_argument(
        "--direction",
        help="Process a single direction string instead of polling Telegram",
    )
    args = parser.parse_args()

    if args.direction:
        ok = absorb_direction(args.direction, dry_run=args.dry_run)
        sys.exit(0 if ok else 1)
    else:
        count = poll_telegram(dry_run=args.dry_run)
        print(f"[absorber] processed {count} direction(s)")
