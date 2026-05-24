#!/usr/bin/env python3
"""Taste-aware heuristic evaluator for particle_art.

Runs after each creator tick. Scores new pieces 0-10 against taste.json,
writes candidates to preferences.json, queues low-score notes to
eval_queue.jsonl, and sends a Telegram summary.

No LLM calls — pure heuristic scoring. Stdlib + requests only.

State: last-run timestamp tracked in ~/.particle_art_eval_state.json.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO    = Path(__file__).resolve().parent.parent
PIECES  = REPO / "pieces"
PREFS   = REPO / "scripts" / "preferences.json"
TASTE   = REPO / "taste.json"
MUT_LOG = REPO / "scripts" / "mutation_log.jsonl"
IMP_LOG = REPO / "scripts" / "improv_log.jsonl"
LINEAGE = REPO / "lineage.json"
EVAL_Q  = REPO / "scripts" / "eval_queue.jsonl"
STATE   = Path.home() / ".particle_art_eval_state.json"


# ── helpers ───────────────────────────────────────────────────────────────────

def _load_json(p: Path) -> dict | list:
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _load_state() -> dict:
    if not STATE.exists():
        return {}
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _save_state(state: dict):
    STATE.write_text(json.dumps(state, indent=2))


def _read_recent_directives(n: int = 5) -> list[str]:
    """Last n directive texts from both mutation and improv logs."""
    out = []
    for log_path in (MUT_LOG, IMP_LOG):
        if not log_path.exists():
            continue
        for ln in log_path.read_text().splitlines():
            try:
                e = json.loads(ln)
                d = e.get("directive_id") or e.get("word") or ""
                if d:
                    out.append(d)
            except Exception:
                pass
    return out[-n:]


def _tokenize(text: str) -> set[str]:
    """Lowercase alphanumeric tokens from a string."""
    import re
    return set(re.findall(r"[a-z0-9]+", text.lower()))


# ── scoring ───────────────────────────────────────────────────────────────────

def score_piece(piece_id: str, meta: dict, taste: dict, prefs_marks: dict,
                recent_directives: list[str]) -> tuple[int, dict]:
    """Score a piece 0-10. Returns (total_score, axis_scores)."""
    likes    = taste.get("likes", {})
    dislikes = taste.get("dislikes", {})

    like_dirs  = [d.lower() for d in (likes.get("directions") or [])]
    dislike_dirs = [d.lower() for d in (dislikes.get("directions") or [])]
    like_techs = [t.lower() for t in (likes.get("techniques") or [])]

    # Corpus: mutation_directive + direction + tags joined
    directive_text = (meta.get("mutation_directive") or "").lower()
    direction_text = (meta.get("direction") or "").lower()
    tags = [t.lower() for t in (meta.get("tags") or [])]
    stack = [s.lower() for s in (meta.get("stack") or [])]

    corpus_tokens = _tokenize(directive_text + " " + direction_text + " " + " ".join(tags))

    # ── axis 1: form_clarity ──────────────────────────────────────────────────
    form_score = 0
    for ld in like_dirs:
        ld_tokens = _tokenize(ld)
        if ld_tokens & corpus_tokens:
            form_score += 1
    form_score = min(form_score, 5)
    for dd in dislike_dirs:
        dd_tokens = _tokenize(dd)
        if dd_tokens & corpus_tokens:
            form_score -= 2
    form_score = max(form_score, 0)

    # ── axis 2: technique_match ────────────────────────────────────────────────
    tech_corpus = corpus_tokens | _tokenize(" ".join(stack))
    tech_score = 0
    for lt in like_techs:
        lt_tokens = _tokenize(lt)
        if lt_tokens & tech_corpus:
            tech_score += 1
    tech_score = min(tech_score, 3)

    # ── axis 3: novelty ───────────────────────────────────────────────────────
    novelty_score = 0
    parent_id = meta.get("parent_id") or ""
    if parent_id and prefs_marks.get(parent_id, {}).get("favorite"):
        novelty_score += 2
    directive_id = (meta.get("mutation_directive_id") or
                    meta.get("improv_word") or
                    meta.get("mutation_directive") or "")
    if directive_id and directive_id not in recent_directives:
        novelty_score += 1
    novelty_score = min(novelty_score, 3)

    axes = {
        "form_clarity":    form_score,
        "technique_match": tech_score,
        "novelty":         novelty_score,
    }
    total = min(form_score + tech_score + novelty_score, 10)
    return total, axes


def _weakest_axis(axes: dict) -> str:
    return min(axes, key=lambda k: axes[k])


def _suggested_directive(weakest: str, meta: dict, taste: dict) -> str:
    """Short concrete directive addressing the weakest scoring axis."""
    likes = taste.get("likes", {})
    if weakest == "form_clarity":
        dirs = likes.get("directions") or []
        hint = dirs[0] if dirs else "recognizable biological silhouette"
        return f"add recognizable form — try: {hint[:80]}"
    if weakest == "technique_match":
        techs = likes.get("techniques") or []
        hint = techs[0] if techs else "CatmullRomCurve3 + TubeGeometry for vascular/ink rendering"
        return f"use preferred technique — try: {hint[:80]}"
    # novelty
    parent_id = meta.get("parent_id") or "?"
    return (f"branch from a user-favorited parent (parent {parent_id} is not marked "
            "favorite) and use a fresh directive not in recent history")


# ── eval_queue read helper (shared with mutate.py / improv_tick.py) ───────────

def read_top_eval_note() -> str:
    """Return oldest unconsumed eval_queue item as a soft-constraint string.

    Returns empty string if queue is empty or all items are consumed.
    Marks the item consumed in ~/.particle_art_eval_state.json.
    """
    if not EVAL_Q.exists():
        return ""
    state = _load_state()
    consumed = set(state.get("consumed_eval_ids", []))

    entries = []
    for ln in EVAL_Q.read_text().splitlines():
        try:
            e = json.loads(ln)
            if e.get("consumed"):
                consumed.add(e["piece_id"] + ":" + e.get("ts", ""))
            entries.append(e)
        except Exception:
            pass

    for e in entries:
        key = e["piece_id"] + ":" + e.get("ts", "")
        if key in consumed:
            continue
        # mark consumed
        consumed.add(key)
        state["consumed_eval_ids"] = list(consumed)
        _save_state(state)
        score = e.get("score", "?")
        weak  = e.get("weakest_axis", "?")
        sugg  = e.get("suggested_directive", "")
        pid   = e["piece_id"]
        return (
            f"Last eval feedback: piece {pid} scored low on {weak} (score {score}/10) "
            f"— suggested: {sugg}. Apply this as a soft constraint."
        )
    return ""


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    taste         = _load_json(TASTE)
    lineage_data  = _load_json(LINEAGE)
    prefs_data    = _load_json(PREFS)
    prefs_marks   = prefs_data.get("marks", {}) if isinstance(prefs_data, dict) else {}
    pieces        = lineage_data.get("pieces", []) if isinstance(lineage_data, dict) else []

    state         = _load_state()
    last_run_ts   = state.get("last_run_ts", "1970-01-01T00:00:00+00:00")

    # parse last run
    try:
        last_dt = datetime.fromisoformat(last_run_ts)
    except Exception:
        last_dt = datetime.min.replace(tzinfo=timezone.utc)

    recent_directives = _read_recent_directives(5)

    new_pieces = []
    for p in pieces:
        created_raw = p.get("created_at", "")
        try:
            created_dt = datetime.fromisoformat(created_raw)
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if created_dt > last_dt:
            new_pieces.append(p)

    candidates_count = 0
    queued_count     = 0
    now_ts = datetime.now(timezone.utc).isoformat()

    for p in new_pieces:
        pid  = p["id"]
        meta_path = PIECES / pid / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
            except Exception:
                meta = dict(p)
        else:
            meta = dict(p)

        total, axes = score_piece(pid, meta, taste, prefs_marks, recent_directives)

        if total >= 7:
            mark = prefs_marks.get(pid, {})
            if not mark.get("favorite") and not mark.get("drop") and not mark.get("candidate"):
                # write candidate mark
                prefs_marks[pid] = dict(mark)
                prefs_marks[pid]["candidate"] = True
                prefs_marks[pid]["note"] = f"auto-eval: score {total}/10"
                candidates_count += 1

        elif total < 4:
            mark = prefs_marks.get(pid, {})
            if not mark.get("drop") and not mark.get("favorite"):
                weak = _weakest_axis(axes)
                sugg = _suggested_directive(weak, meta, taste)
                entry = {
                    "piece_id":           pid,
                    "score":              total,
                    "axes":               axes,
                    "weakest_axis":       weak,
                    "suggested_directive": sugg,
                    "ts":                 now_ts,
                }
                with EVAL_Q.open("a") as f:
                    f.write(json.dumps(entry) + "\n")
                queued_count += 1

    # persist updated preferences.json if any candidates were added
    if candidates_count > 0:
        prefs_data["marks"] = prefs_marks
        PREFS.write_text(json.dumps(prefs_data, indent=2))

    # update last_run_ts
    state["last_run_ts"] = now_ts
    _save_state(state)

    total_new = len(new_pieces)
    print(f"evaluator: {total_new} new pieces scored, {candidates_count} candidates, {queued_count} queued")

    # Telegram summary
    try:
        import requests
        token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("ALLOWED_CHAT_ID", "")
        if token and chat_id:
            msg = (
                f"\U0001f50d *Evaluator*: {total_new} new pieces scored\n"
                f"↑ {candidates_count} candidates (score ≥7)\n"
                f"↓ {queued_count} low-score notes queued"
            )
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
                timeout=10,
            )
    except Exception as e:
        print(f"telegram: skipped ({e})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
