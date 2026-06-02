#!/usr/bin/env python3
"""Improv tick — random word + morph-and-repeat at 12-min cadence.

User's request (2026-05-06):
  "apply improv technique — think of a random word, build in that
   imagery/concept/vibe, morph and repeat. every 12 mins, 120 piece per day."

Mechanics:
  1. Pick a parent piece. Default policy: 60% most-recent piece in lineage
     (creates a chain that reads as continuous improv), 25% random favorite
     (occasional jump to a fresh thread), 15% any piece in pool (long-tail
     branching). This keeps the chain feel without collapsing the gallery
     into a single deep thread.
  2. Sample a word from scripts/word_movements.json. Avoid the last 12
     words used (system-wide), so the vibe doesn't get stuck.
  3. Build an "improv" prompt: the word IS the directive — its concept,
     symbol, sensory texture, mythic register — applied as a morph of the
     parent. Reuse parent's HTML so morph stays continuous.
  4. Generate via lib_claude (subscription primary, Bedrock fallback).
  5. Validate, write a NEW top-level lineage piece (not a movements/ child),
     append to lineage.json with directive_id = "improv:<word>".
  6. Commit + push.

Designed for cron (no interactive prompts). Honors budget.py caps.
"""
import argparse
import json
import os
import random
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import budget
import codes
import element_counts
import lib_claude
from evaluator import read_top_eval_note
from lineage_lock import lineage_write_lock

LINEAGE = REPO / "lineage.json"
PIECES  = REPO / "pieces"
PREFS   = REPO / "scripts" / "preferences.json"
WORDS   = REPO / "scripts" / "word_movements.json"
SURPRISE_WORDS = REPO / "scripts" / "surprise_words.json"
ARTIST_PERSONALITIES = REPO / "scripts" / "artist_personalities.json"
LOG     = REPO / "scripts" / "improv_log.jsonl"

# mode-sample distribution. Sums to 1.0.
#
# Tuned 2026-05-07 from empirical data over 68 scored pieces + 106 thumbnails:
#   surprise: score-mean=6.94, px-coverage-mean=18.1%  (best in both)
#   artist:   score-mean=6.84, px-coverage-mean=12.2%  (mid)
#   chain:    score-mean=6.72, px-coverage-mean=14.7%  (weakest, also
#             dominates bottom-quartile of weak pieces 11/25)
# So we lean toward surprise/artist branches (which produce more legible
# distinct work) and away from chain (which drifts after gen ≥ 4).
MODE_WEIGHTS = {"chain": 0.30, "surprise": 0.40, "artist": 0.30}


def load_json(p: Path) -> dict:
    return json.loads(p.read_text())


def _style_key_from_tags(tags: list[str]) -> str:
    """Compact style bucket for lineage tree grouping (stored in lineage.json)."""
    for t in tags:
        if t.startswith("channel:"):
            return t
    for t in tags:
        if t.startswith("mode:"):
            return t
    directives = [t for t in tags if ":" not in t and t not in (
        "improv", "cross-pollinated", "volumetric-light", "multi-state", "object-cloud",
    )]
    return directives[-1] if directives else "directive"


def _read_recent_log(n: int = 50) -> list[dict]:
    if not LOG.exists():
        return []
    out = []
    for ln in LOG.read_text().splitlines()[-n:]:
        try:
            out.append(json.loads(ln))
        except Exception:
            pass
    return out


def sample_mode() -> str:
    keys = list(MODE_WEIGHTS.keys())
    weights = [MODE_WEIGHTS[k] for k in keys]
    return random.choices(keys, weights=weights, k=1)[0]


def pick_parent(lineage: dict, prefs: dict, mode: str) -> tuple[dict, str]:
    """Pick a parent piece per mode.

    chain   → most-recent non-dropped piece (continues the thread).
    surprise / artist → favorite (preferred) else any non-dropped piece, NEVER
        the most-recent piece (these modes are explicitly NEW BRANCHES per
        user direction 2026-05-06).

    Returns (parent_piece, policy_tag).
    """
    pieces = lineage["pieces"]
    if not pieces:
        raise RuntimeError("no pieces in lineage")
    marks = prefs.get("marks", {})
    favored = [p for p in pieces if marks.get(p["id"], {}).get("favorite")]
    dropped = {pid for pid, m in marks.items() if m.get("drop")}
    pool = [p for p in pieces if p["id"] not in dropped] or pieces

    if mode == "chain":
        return pool[-1], "recent"

    # NEW-BRANCH modes: skip the most-recent piece so we don't accidentally
    # extend the existing chain.
    most_recent_id = pool[-1]["id"]
    branchable_favored = [p for p in favored if p["id"] != most_recent_id]
    branchable_pool    = [p for p in pool    if p["id"] != most_recent_id] or pool
    if branchable_favored and random.random() < 0.7:
        return random.choice(branchable_favored), "favorite-branch"
    return random.choice(branchable_pool), "any-branch"


def _load_word_pool(path: Path, key: str) -> list:
    """Read a word vocab file. `key` is 'words' or 'personalities'."""
    return json.loads(path.read_text())[key]


def sample_word(mode: str, recent: list[dict]) -> tuple[str, dict]:
    """Pick a word for this mode, skipping the last 12 system-wide and
    inverse-frequency-biasing toward under-used items across all logs.

    Returns (word, extra_meta).
        extra_meta carries mode-specific extras (e.g. artist name+practice).
    """
    used_recent = {r.get("word") for r in recent[-12:] if r.get("word")}

    if mode == "artist":
        pool = _load_word_pool(ARTIST_PERSONALITIES, "personalities")
        candidates = [e for e in pool if e["word"] not in used_recent] or pool
        # bias by per-mode count so over-channeled artists fade
        counts = element_counts.word_counts(mode="artist")
        weights = element_counts.bias_weights(
            candidates, [1.0] * len(candidates), counts,
            key=lambda e: e["word"], beta=1.0,
        )
        chosen = random.choices(candidates, weights=weights, k=1)[0]
        return chosen["word"], {"artist": chosen["artist"], "practice": chosen["practice"]}

    if mode == "surprise":
        pool = _load_word_pool(SURPRISE_WORDS, "words")
    else:
        pool = _load_word_pool(WORDS, "words")
    candidates = [w for w in pool if w not in used_recent] or pool
    counts = element_counts.word_counts()  # global word usage across logs
    weights = element_counts.bias_weights(
        candidates, [1.0] * len(candidates), counts,
        key=lambda w: w, beta=1.0,
    )
    return random.choices(candidates, weights=weights, k=1)[0], {}


def _read_taste() -> dict:
    p = REPO / "taste.json"
    if not p.exists(): return {}
    try: return json.loads(p.read_text())
    except Exception: return {}


def _rejection_block() -> str:
    """Mirror of mutate.py — inject taste guardrails into every prompt."""
    taste = _read_taste()
    lines = []
    dislikes = taste.get("dislikes", {})
    if dislikes.get("directions"):
        lines.append("Directions the user has rejected — DO NOT produce:")
        for d in dislikes["directions"]:
            lines.append(f"  - {d}")
    if dislikes.get("techniques"):
        lines.append("Technical patterns that have failed silently — AVOID:")
        for t in dislikes["techniques"]:
            lines.append(f"  - {t}")
    principles = taste.get("principles") or []
    if principles:
        lines.append("Principles the user holds — every output should respect:")
        for p in principles:
            lines.append(f"  - {p}")
    return "\n".join(lines) if lines else ""


def build_prompt(parent: dict, parent_html: str, word: str,
                 mode: str, extras: dict,
                 user_directive: str | None = None) -> tuple[str, str]:
    rejection = _rejection_block()
    eval_note = read_top_eval_note()

    # User-submitted directive overrides mode framing entirely.
    if user_directive:
        framing = (
            f"## User direction (highest priority — implement this directly)\n"
            f"{user_directive}\n\n"
            "Apply this request as a CONTINUOUS MORPH of the parent piece. "
            "Implement every named effect faithfully. Keep all WebGL/three.js "
            "plumbing intact; only change what the request targets.\n"
        )
    # Mode-specific framing inserted into the user message.
    elif mode == "chain":
        framing = (
            f"## Word\n**{word}**\n\n"
            "Treat this word as the directive. What does it _feel_ like? What "
            "concept, symbol, narrative, sensory texture, mythic register does "
            "it carry? Apply it as a CONTINUOUS MORPH of the parent — the new "
            "piece must read as the parent after the word has passed through "
            "it, not a fresh start.\n"
        )
    elif not user_directive and mode == "surprise":
        framing = (
            f"## Word (SURPRISE-TURN mode)\n**{word}**\n\n"
            "This is a SURPRISE/DELIGHT branch. Take the parent piece as your "
            "starting form, then introduce ONE clear surprise — a sudden "
            "tonal shift, a comic timing beat, an unexpected scale flip, a "
            "color reversal, a momentary glitch, a non-sequitur element — "
            "informed by the word above. The surprise should land like a "
            "Calder mobile suddenly tipping, or a Tinguely machine starting "
            "to hum: the form was orderly, then briefly cheeky, then "
            "self-recovers. Keep the surprise tasteful (no jump-scare); "
            "playful, not chaotic.\n"
        )
    elif not user_directive and mode == "artist":
        artist = extras.get("artist", "?")
        practice = extras.get("practice", "")
        framing = (
            f"## Personality word\n**{word}** — channel **{artist}**\n"
            f"Practice: {practice}\n\n"
            f"This is an ARTIST-PERSONALITY branch. Take the parent piece as "
            f"a starting form, but morph it as if {artist} themselves were "
            f"taking over: their sensibility, their pacing, their pet "
            f"materials, their characteristic compositional habit. The "
            f"personality word **{word}** is the lead — let it set the tempo "
            f"and gesture. The piece should feel like {artist} working in "
            f"particle-art medium, NOT like a literal pastiche of their "
            f"famous works.\n"
        )
    elif not user_directive:
        framing = f"## Word\n**{word}**\n"

    system = (
        "You are an improv engine for a particle-art evolutionary gallery. "
        "Each tick the system gives you ONE WORD and ONE PARENT PIECE plus "
        "a MODE (chain / surprise / artist). Your job is to feel the word "
        "and follow the mode's framing to produce ONE self-contained HTML "
        "file (three.js via importmap from unpkg.com, all GLSL inline, no "
        "build step, no external CSS/JS files beyond CDN imports). Keep "
        "the file under 600 lines. Preserve a small id label fixed "
        "bottom-left (font-family ui-monospace, color #cdd2dc, opacity "
        "0.55, font-size 11px); the 3-char id will be supplied as "
        "<NEW_ID>.\n\n"
        + (("USER TASTE GUARDRAILS — read carefully:\n" + rejection + "\n\n") if rejection else "")
        + "Reflexes to engage in EVERY mode:\n"
        + "  - Form must remain RECOGNIZABLE; no noise blobs.\n"
        + "  - Subject must occupy >10% of frame at default camera.\n"
        + "  - Camera should track the centroid as the form grows.\n"
        + "  - Restrained palette (single color or duotone) almost always wins.\n"
        + "  - Validate ShaderMaterial fog plumbing (use fragmentShader-side "
            "fog mixing; never set `fog: true` + raw uniforms — silent compile fail).\n"
    )

    eval_prefix = (eval_note + "\n\n") if eval_note else ""
    user = f"""{eval_prefix}# Improv tick (mode: {mode})

{framing}
## Parent piece metadata
```json
{json.dumps({k: v for k, v in parent.items() if k not in {"created_at"}}, indent=2)}
```

## Parent piece HTML (modify, don't replace)
```html
{parent_html}
```

## Output
Reply with ONLY the new HTML file content. Start with `<!doctype html>`. No
prose, no markdown fences, no explanation. The id label in the bottom-left
should read the literal string `<NEW_ID>`.
"""
    return system, user


# ---- shared with mutate.py (kept inline so improv_tick is standalone) ----

def extract_html(text: str) -> str:
    m = re.search(r"```(?:html)?\s*(<!doctype.*?</html>)\s*```", text, re.S | re.I)
    if m:
        return m.group(1)
    m = re.search(r"<!doctype.*?</html>", text, re.S | re.I)
    if m:
        return m.group(0)
    raise ValueError("no <!doctype html>…</html> found in response")


def validate(html: str) -> None:
    if "<!doctype html>" not in html.lower():
        raise ValueError("missing doctype")
    if "</html>" not in html.lower():
        raise ValueError("missing </html>")
    if "three" not in html.lower():
        raise ValueError("does not reference three.js")
    if "<script" not in html.lower():
        raise ValueError("no <script>")
    if len(html) < 1500:
        raise ValueError(f"suspiciously small ({len(html)} bytes)")


def run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=check)


def append_log(entry: dict):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def _generate_one(parent: dict, parent_html: str, word: str,
                  mode: str, extras: dict, refine_feedback: str | None = None,
                  user_directive: str | None = None,
                  ) -> tuple[str, str]:
    """Run one generation pass. Optionally append critic feedback.

    Returns (extracted_html, provider). Raises on extraction/validation failure.
    """
    system, user = build_prompt(parent, parent_html, word, mode, extras,
                                user_directive=user_directive)
    if refine_feedback:
        user = (user + "\n\n## Critic feedback from a prior attempt — fix these in this version:\n"
                + refine_feedback + "\n")
    text, provider = lib_claude.call(system, user)
    html = extract_html(text)
    validate(html)
    return html, provider


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="generate but don't commit/push")
    ap.add_argument("--no-push", action="store_true", help="commit but don't push")
    ap.add_argument("--mode", choices=list(MODE_WEIGHTS.keys()), help="force a mode")
    ap.add_argument("--word", help="force a specific word")
    ap.add_argument("--parent", help="force a specific parent id")
    ap.add_argument("--no-critic", action="store_true",
                    help="skip the critic+refine step (faster, lower quality)")
    ap.add_argument("--user-directive", default=None,
                    help="user-submitted feedback text (overrides word/mode framing)")
    args = ap.parse_args()

    # budget gate. Two budget calls per tick (gen + refine) plus 2 critic
    # calls = ~4× the per-tick cost on Bedrock. Subscription = $0.
    ok, why = budget.can_spend(lib_claude.BEDROCK_COST_ESTIMATE_USD * 4)
    if not ok and not args.dry_run:
        print(f"abort: budget — {why}")
        return 2

    lineage = load_json(LINEAGE)
    prefs = load_json(PREFS)
    recent = _read_recent_log(50)

    mode = args.mode or sample_mode()

    if args.parent:
        cand = [p for p in lineage["pieces"] if p["id"] == args.parent]
        if not cand:
            print(f"unknown parent: {args.parent}"); return 3
        parent, policy = cand[0], "manual"
    else:
        parent, policy = pick_parent(lineage, prefs, mode)

    parent_html = (PIECES / parent["id"] / "index.html").read_text()
    if args.user_directive:
        word, extras = args.user_directive[:60], {}
        mode = "chain"  # user directives are continuous morphs
    elif args.word:
        word, extras = args.word, {}
    else:
        word, extras = sample_word(mode, recent)

    print(f"mode: {mode}")
    print(f"parent: {parent['id']} ({parent.get('title','')}) [policy={policy}]")
    print(f"word: {word}" + (f"  (artist={extras.get('artist')})" if extras.get("artist") else ""))

    user_dir = args.user_directive

    # === Generation v1 ===
    try:
        html_v1, provider_v1 = _generate_one(parent, parent_html, word, mode, extras,
                                             user_directive=user_dir)
    except lib_claude.ProviderError as e:
        print(f"provider failure (v1): {e}"); return 4
    except Exception as e:
        print(f"v1 rejected: {e}"); return 5
    print(f"v1 generated ({len(html_v1)} bytes, provider={provider_v1})")

    # === Critic + refine + critic ===
    final_html, final_provider, score_log = html_v1, provider_v1, {"v1": None, "v2": None, "picked": "v1"}
    html_v2_holder: dict = {}  # populated only if v2 succeeds
    if not args.no_critic:
        import critic  # local import; only loaded on real ticks
        s1 = critic.judge(html_v1, mode, word, extras, parent["id"], parent.get("title", ""))
        score_log["v1"] = s1
        print(f"critic v1: exec={s1['execution_score']} aes={s1['aesthetic_score']} → {s1['combined']:.1f}")
        # refinement attempt
        try:
            html_v2, provider_v2 = _generate_one(parent, parent_html, word, mode, extras,
                                                 refine_feedback=s1["feedback"],
                                                 user_directive=user_dir)
            html_v2_holder["html"] = html_v2
            s2 = critic.judge(html_v2, mode, word, extras, parent["id"], parent.get("title", ""))
            score_log["v2"] = s2
            print(f"critic v2: exec={s2['execution_score']} aes={s2['aesthetic_score']} → {s2['combined']:.1f}")
            if s2["combined"] > s1["combined"]:
                final_html, final_provider, score_log["picked"] = html_v2, provider_v2, "v2"
        except Exception as e:
            print(f"v2 failed (keeping v1): {e}")
        print(f"picked: {score_log['picked']}")

    new_id = codes.generate(LINEAGE, n=1)[0]
    final_html = final_html.replace("<NEW_ID>", new_id)
    print(f"new_id: {new_id}")

    # static precheck — catches hard-banned code patterns before disk write
    from precheck import run as _precheck
    _pc = _precheck(final_html)
    if not _pc["passed"]:
        for _v in _pc["violations"]:
            print(f"precheck REJECT [{_v['id']}]: {_v['description']}")
            print(f"  match: {_v['match']!r}")
            if _v.get("fix"):
                print(f"  fix:   {_v['fix']}")
        rej = REPO / "scripts" / f"reject_{new_id}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.html"
        rej.write_text(final_html)
        print(f"precheck-rejected HTML saved to {rej.name}")
        return 6
    for _w in _pc.get("warnings", []):
        print(f"precheck WARN [{_w['id']}]: {_w['match']!r}")

    if args.dry_run:
        print(f"[dry-run] would create pieces/{new_id}/  (parent={parent['id']}, word={word}, mode={mode}, provider={final_provider}, picked={score_log['picked']})")
        return 0

    out_dir = PIECES / new_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(final_html)

    # ── render-content gate ──────────────────────────────────────────
    # Mirrors mutate.py's gate (commit 12b5182). Blank/black pieces
    # (shader compile failure, design that waits for an input that never
    # comes, misframed camera, missing model load, network race) must
    # NEVER ship to Vercel — Playwright headless render here, before commit.
    #
    # If render < threshold pixels, the piece is wiped and the tick
    # exits with rc=7 so improv_cron.sh skips the push retry block
    # entirely (no commit to retry).
    try:
        import shutil as _shutil
        from validate_render import validate as render_validate
        rc, failures = render_validate([new_id], record_clip=False, return_details=True)
        if rc != 0:
            rej = REPO / "scripts" / f"reject_{new_id}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.html"
            rej.write_text(final_html)
            is_blur = any("blurry" in reason for _, reason in failures)
            reason_str = failures[0][1] if failures else "unknown"
            print(f"render gate: REJECTED {new_id} — {reason_str} (saved to {rej.name})")
            _shutil.rmtree(out_dir, ignore_errors=True)
            thumb = REPO / "thumbs" / f"{new_id}.png"
            if thumb.exists():
                thumb.unlink()
            if is_blur:
                # queue sharpness constraint for next tick to consume via evaluator
                import json as _json
                eval_q = REPO / "scripts" / "eval_queue.jsonl"
                entry = {
                    "piece_id": new_id,
                    "score": 0,
                    "axes": {"form_clarity": 0, "technique_match": 0, "novelty": 0},
                    "weakest_axis": "render_quality",
                    "suggested_directive": (
                        "SHARPNESS REQUIRED: previous piece was blurry — "
                        "use PointsMaterial sizeAttenuation=false, opacity≥0.8, "
                        "no soft halos or Gaussian-blur sprites"
                    ),
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
                with eval_q.open("a") as f:
                    f.write(_json.dumps(entry) + "\n")
                print(f"  blur failure queued to eval_queue.jsonl — next tick will apply sharpness constraint")
            return 7
        print(f"render gate: PASS {new_id}")
    except ImportError as e:
        print(f"render gate: SKIPPED ({e}) — pip install playwright pillow numpy && playwright install chromium")
    except Exception as e:
        print(f"render gate: SKIPPED on exception ({e})")

    # Save the loser for inspection (gitignored).
    loser_label = "v2" if score_log["picked"] == "v1" else "v1"
    loser_html = None
    if loser_label == "v1":
        loser_html = html_v1
    elif "html" in html_v2_holder:
        loser_html = html_v2_holder["html"]
    if loser_html is not None and score_log.get(loser_label):
        rejects_dir = REPO / "scripts" / "rejects" / new_id
        rejects_dir.mkdir(parents=True, exist_ok=True)
        (rejects_dir / f"{loser_label}.html").write_text(loser_html.replace("<NEW_ID>", new_id))
        (rejects_dir / "scores.json").write_text(json.dumps(score_log, indent=2))

    directive_id = f"improv:{mode}:{word}"
    new_meta = {
        "id": new_id,
        "title": f"improv·{mode} · {word}" + (f" ({extras.get('artist')})" if extras.get("artist") else ""),
        "direction": parent.get("direction", ""),
        "stack": parent.get("stack", []),
        "input": parent.get("input", "unknown"),
        "particle_count": parent.get("particle_count"),
        "parent_id": parent["id"],
        "generation": parent.get("generation", 0) + 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mutation_directive": f"improv {mode}: {word}" + (f" via {extras.get('artist')}" if extras.get("artist") else ""),
        "mutation_directive_id": directive_id,
        "provider": final_provider,
        "tags": (parent.get("tags") or []) + [f"word:{word}", f"mode:{mode}", "improv"]
                + ([f"artist:{extras.get('artist','').replace(' ','_')}"] if extras.get("artist") else []),
        "improv_word": word,
        "improv_mode": mode,
        "improv_policy": policy,
        "improv_extras": extras,
        "critic_scores": score_log,
    }
    (out_dir / "meta.json").write_text(json.dumps(new_meta, indent=2, ensure_ascii=False))

    # re-read under lock so concurrent workers don't clobber each other
    with lineage_write_lock():
        lineage = load_json(LINEAGE)
        lineage["pieces"].append({
            "id": new_id,
            "title": new_meta["title"],
            "direction": new_meta["direction"],
            "input": new_meta["input"],
            "particle_count": new_meta["particle_count"],
            "parent_id": parent["id"],
            "generation": new_meta["generation"],
            "created_at": new_meta["created_at"],
            "style_key": _style_key_from_tags(new_meta["tags"]),
        })
        lineage.setdefault("edges", []).append({
            "from": parent["id"], "to": new_id, "directive": directive_id,
        })
        lineage["updated_at"] = datetime.now(timezone.utc).isoformat()
        LINEAGE.write_text(json.dumps(lineage, indent=2, ensure_ascii=False))

    # Cost: gen v1 + critic v1 + gen v2 + critic v2 = 4 calls. Subscription = $0.
    n_calls = 4 if not args.no_critic else 1
    cost = 0.0 if final_provider == "subscription" else lib_claude.BEDROCK_COST_ESTIMATE_USD * n_calls
    budget.record(cost, final_provider, note=f"improv {mode} {parent['id']}->{new_id} ({word})")
    append_log({
        "ts": new_meta["created_at"], "id": new_id, "parent": parent["id"],
        "word": word, "mode": mode, "policy": policy,
        "directive_id": directive_id, "provider": final_provider, "cost_usd": cost,
        "critic": {
            "v1": score_log.get("v1") and {k: score_log["v1"][k] for k in ("execution_score", "aesthetic_score", "combined")},
            "v2": score_log.get("v2") and {k: score_log["v2"][k] for k in ("execution_score", "aesthetic_score", "combined")},
            "picked": score_log["picked"],
        },
    })

    print(f"created: pieces/{new_id}/  (parent={parent['id']}, word={word}, mode={mode}, gen={new_meta['generation']}, provider={final_provider})")

    run_git("add",
            f"pieces/{new_id}",
            "lineage.json",
            "taste.json",
            "scripts/improv_log.jsonl")
    # Reject artifacts intentionally NOT pushed (kept local for inspection).
    msg = f"improv·{mode} {parent['id']} → {new_id} · {word}"
    run_git("commit", "-m", msg)
    if not args.no_push:
        push = run_git("push", check=False)
        if push.returncode != 0:
            print(f"push failed: {push.stderr}")
            return 6
    print(f"committed: {msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
