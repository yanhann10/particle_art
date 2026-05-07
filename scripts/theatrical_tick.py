#!/usr/bin/env python3
"""Theatrical-movement experiment — autonomous version of the user's prompt:
'think of a random word, then apply the concept/symbol/meaning/narrative/
imagery of that word to a chosen art piece, blend it in, repeat — so one
art piece can have several movements, as if it's theatrical art.'

Architecture:
  - One TARGET PIECE per "play" (default: 5gm — user-confirmed favorite).
  - Each tick produces ONE new MOVEMENT, stored as a sibling file under
    pieces/<target>/movements/<N>/index.html with its own meta entry
    in pieces/<target>/movements.json.
  - The target's main pieces/<target>/index.html is left untouched (5gm
    stays as the user knows it). The movements live alongside.
  - A separate player at pieces/<target>/play.html cycles through all
    movements end-to-end (auto-advance every ~12s, click to next, ESC
    to return to gallery).

Each tick:
  1. Sample a word from scripts/word_movements.json that hasn't been used
     recently for this target piece.
  2. Build a prompt: parent's HTML + the word + its sense as the artist's
     intuitive starting point. Ask Claude for a new SELF-CONTAINED HTML
     piece that interprets the word as a movement of the parent's form.
  3. Validate (same scaffold check as the mutation worker).
  4. Save as pieces/<target>/movements/m<N>/index.html.
  5. Update pieces/<target>/movements.json with {n, word, created_at,
     provider, prompt_sha} entry.
  6. Regenerate pieces/<target>/play.html (small player file).
  7. Commit + push.

Usage:
  scripts/theatrical_tick.py <target_piece_id>     # one tick

Cron-friendly. Gracefully respects the same budget guard as mutate.py.
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

import budget          # noqa
import lib_claude      # noqa

PIECES = REPO / "pieces"
WORDS = REPO / "scripts" / "word_movements.json"
LOG   = REPO / "scripts" / "theatrical_log.jsonl"


def load_words() -> list[str]:
    return json.loads(WORDS.read_text())["words"]


def sample_word(target_id: str) -> str:
    pool = load_words()
    movs_path = PIECES / target_id / "movements.json"
    used = []
    if movs_path.exists():
        try:
            used = [m["word"] for m in json.loads(movs_path.read_text()).get("movements", [])]
        except Exception:
            used = []
    # avoid the last 6 used words; if the pool is exhausted, allow repeats
    recent = set(used[-6:])
    candidates = [w for w in pool if w not in recent] or pool
    return random.choice(candidates)


def build_prompt(parent_html: str, word: str, target_id: str, parent_meta: dict) -> tuple[str, str]:
    parent_summary = json.dumps({
        "id": target_id,
        "title": parent_meta.get("title", ""),
        "direction": parent_meta.get("direction", ""),
        "stack": parent_meta.get("stack", []),
        "input": parent_meta.get("input", ""),
    }, indent=2)

    system = (
        "You are an artist creating a new MOVEMENT of an existing piece — "
        "like one act of a theatrical play. The form is the same column / curve / "
        "growth the parent piece established; the LIGHT, MOOD, SOUND, PALETTE, "
        "and TIMING shift to express the essence of a single word. "
        "Output ONE self-contained HTML file (three.js via importmap from unpkg.com, "
        "all GLSL inline, no external CSS/JS files beyond CDN imports), runs by opening "
        "the file in a modern browser, under 800 lines. Keep a small 3-char id label "
        "fixed bottom-left in monospace at 11px opacity 0.55 (the id will be supplied)."
    )
    user = f"""# Theatrical movement

## The parent piece
```json
{parent_summary}
```

## Parent piece HTML (the existing form — keep its skeleton, change its mood)
```html
{parent_html}
```

## Word for this movement
**{word}**

Read the word's full register: dictionary meaning, etymology if relevant, the
emotional climate it carries, the imagery a poet would associate with it, the
narrative or ritual it implies. Now produce a new HTML file that takes the parent's
underlying form (its growth dynamics, its geometric scaffold) and reinterprets
it as a movement under this word's spell.

Concrete things you can change to express the word:
  - PALETTE — every word has its own light
  - PACING — some words demand slowness, others sudden bursts, others stillness
  - SOUND — if the word implies sound (susurrus, drone, silence), wire mic input
    or generated tone in service of the word's register
  - GEOMETRY OVERLAY — add a single secondary form that COMPLETES the parent
    in the word's spirit (a ripple, a thread, a shaft of light, a hand)
  - CAMERA BEHAVIOR — held still vs. drifting vs. circling vs. rising

Constraints:
  - Keep the parent's CORE growth / curve / sampled form recognizable. The
    viewer should still sense it's the same lineage.
  - No on-screen text from the word itself. The word is felt, not read. Only
    the bottom-left id label uses text.
  - Self-contained HTML. Modern browser. Under 800 lines.
  - Must produce something visible within 8 seconds of page load.

The id label should read the literal string `<NEW_ID>` — I will substitute the
actual short id after generation.

Reply with ONLY the new HTML file content. Start with `<!doctype html>`. No prose,
no markdown fences, no explanation.
"""
    return system, user


def extract_html(text: str) -> str:
    m = re.search(r"```(?:html)?\s*(<!doctype.*?</html>)\s*```", text, re.S | re.I)
    if m: return m.group(1)
    m = re.search(r"<!doctype.*?</html>", text, re.S | re.I)
    if m: return m.group(0)
    raise ValueError("no <!doctype html>…</html> found in response")


def validate(html: str):
    if "<!doctype html>" not in html.lower(): raise ValueError("missing doctype")
    if "</html>" not in html.lower():        raise ValueError("missing </html>")
    if "three" not in html.lower():           raise ValueError("does not reference three.js")
    if "<script" not in html.lower():        raise ValueError("no <script>")
    if len(html) < 1500:                     raise ValueError(f"suspiciously small ({len(html)} bytes)")


PLAYER_TEMPLATE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{TITLE} · theatrical movements</title>
<style>
  html, body {{ margin:0; padding:0; height:100%; background:#000; color:#cdd2dc;
                font-family: ui-monospace, "SF Mono", Menlo, monospace; overflow:hidden; }}
  iframe {{ display:block; width:100vw; height:100vh; border:0; background:#000; }}
  .top {{ position:fixed; left:0; right:0; top:0; padding:0.7rem 1.2rem;
          background: linear-gradient(180deg, rgba(0,0,0,0.85), rgba(0,0,0,0));
          display:flex; justify-content:space-between; align-items:center;
          font-size: 0.78rem; pointer-events:none; z-index:10; }}
  .top .label {{ pointer-events:auto; }}
  .top .label .v {{ color: #c9a86a; font-weight:600; margin-right:0.5rem; }}
  .top .nav {{ display:flex; gap:0.4rem; pointer-events:auto; }}
  .top .nav button {{ background:rgba(20,20,26,0.6); color:#cdd2dc;
       border:1px solid #1f1f26; border-radius:3px; padding:4px 10px;
       cursor:pointer; font-family:inherit; font-size:0.74rem; }}
  .top .nav button:hover {{ border-color:#c9a86a; color:#c9a86a; }}
  .top a {{ color:#8a8a93; text-decoration:none; border-bottom:1px dotted #8a8a93; pointer-events:auto; }}
  .top a:hover {{ color:#c9a86a; border-color:#c9a86a; }}
</style></head><body>
<div class="top">
  <div class="label"><span class="v" id="vlabel">m1</span><span id="word"></span></div>
  <div class="nav">
    <button id="prev">← prev</button>
    <button id="next">next →</button>
    <button id="autopause">pause autoplay</button>
  </div>
  <a href="../../index.html">← gallery</a>
</div>
<iframe id="frame" src="" allow="camera; microphone; autoplay"></iframe>
<script>
const movements = {MOVEMENTS_JSON};
let idx = 0;
let autoplay = true;
const frame = document.getElementById('frame');
const vlabel = document.getElementById('vlabel');
const wordEl = document.getElementById('word');
function render(){{
  const m = movements[idx];
  frame.src = `movements/m${{m.n}}/index.html`;
  vlabel.textContent = `m${{m.n}}`;
  wordEl.textContent = `· ${{m.word}}`;
}}
document.getElementById('prev').onclick = () => {{ idx = (idx-1+movements.length)%movements.length; render(); }};
document.getElementById('next').onclick = () => {{ idx = (idx+1)%movements.length; render(); }};
document.getElementById('autopause').onclick = (e) => {{
  autoplay = !autoplay;
  e.target.textContent = autoplay ? 'pause autoplay' : 'resume autoplay';
}};
addEventListener('keydown', e => {{ if (e.key === 'Escape') location.href = '../../index.html'; }});
setInterval(() => {{ if (autoplay) {{ idx = (idx+1) % movements.length; render(); }} }}, 14000);
render();
</script>
</body></html>
"""


def render_player(target_id: str, movements: list[dict], parent_title: str):
    """Regenerate the play.html for this target, reflecting all current movements."""
    js_movs = json.dumps([{"n": m["n"], "word": m["word"]} for m in movements])
    html = (PLAYER_TEMPLATE
            .replace("{TITLE}", parent_title or target_id)
            .replace("{MOVEMENTS_JSON}", js_movs))
    (PIECES / target_id / "play.html").write_text(html)


def run_git(*args, check: bool = True):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=check)


def append_log(entry: dict):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f: f.write(json.dumps(entry) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="piece id to add a movement to (e.g. 5gm)")
    ap.add_argument("--word", help="force a specific word")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    target_id = args.target
    target_dir = PIECES / target_id
    if not (target_dir / "index.html").exists():
        print(f"unknown target piece: {target_id}"); return 3

    ok, why = budget.can_spend(lib_claude.BEDROCK_COST_ESTIMATE_USD)
    if not ok and not args.dry_run:
        print(f"abort: budget — {why}"); return 2

    parent_html = (target_dir / "index.html").read_text()
    parent_meta = {}
    if (target_dir / "meta.json").exists():
        try: parent_meta = json.loads((target_dir / "meta.json").read_text())
        except Exception: pass

    word = args.word or sample_word(target_id)
    print(f"target: {target_id} ({parent_meta.get('title','')})")
    print(f"word:   {word}")

    system, user = build_prompt(parent_html, word, target_id, parent_meta)

    try:
        text, provider = lib_claude.call(system, user)
    except lib_claude.ProviderError as e:
        print(f"provider failure: {e}"); return 4

    try:
        html = extract_html(text); validate(html)
    except Exception as e:
        print(f"output rejected: {e}")
        rej = REPO / "scripts" / f"reject_theatrical_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.txt"
        rej.write_text(text); print(f"raw saved: {rej}"); return 5

    # next movement number
    movs_path = target_dir / "movements.json"
    if movs_path.exists():
        try: movs = json.loads(movs_path.read_text())
        except Exception: movs = {"movements": []}
    else:
        movs = {"movements": []}
    n = len(movs["movements"]) + 1

    # short id for the label inside the movement HTML
    new_id = f"{target_id}.m{n}"
    html = html.replace("<NEW_ID>", new_id)

    out_dir = target_dir / "movements" / f"m{n}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(html)

    # ── render-content gate ──────────────────────────────────────────
    # Movements live outside pieces/<id>/index.html so we use the
    # path-based companion API. If the headless render produces
    # essentially nothing (shader compile fail, missing model load,
    # design that waits for an input that never arrives in headless),
    # wipe the movement and abort the tick — it must not ship to Vercel.
    try:
        import shutil as _shutil
        from validate_render import validate_html_path
        ok, reason = validate_html_path(
            out_dir / "index.html",
            label=f"{target_id}_m{n}",
            warmup_ms=2500,
        )
        if not ok:
            rej = REPO / "scripts" / f"reject_theatrical_{target_id}_m{n}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.html"
            rej.write_text(html)
            print(f"render gate: REJECTED {target_id}.m{n} ({reason}) — saved to {rej.name}")
            _shutil.rmtree(out_dir, ignore_errors=True)
            return 7
        print(f"render gate: PASS {target_id}.m{n} ({reason})")
    except ImportError as e:
        print(f"render gate: SKIPPED ({e}) — pip install playwright pillow numpy && playwright install chromium")
    except Exception as e:
        print(f"render gate: SKIPPED on exception ({e})")

    movs["movements"].append({
        "n": n,
        "word": word,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
        "path": f"movements/m{n}/index.html",
    })
    movs_path.write_text(json.dumps(movs, indent=2))

    render_player(target_id, movs["movements"], parent_meta.get("title", target_id))

    cost = 0.0 if provider == "subscription" else lib_claude.BEDROCK_COST_ESTIMATE_USD
    budget.record(cost, provider, note=f"theatrical {target_id}.m{n} ({word})")
    append_log({
        "ts": datetime.now(timezone.utc).isoformat(),
        "target": target_id, "n": n, "word": word, "provider": provider, "cost_usd": cost,
    })

    print(f"created: pieces/{target_id}/movements/m{n}/  (word={word}, provider={provider})")

    if args.dry_run:
        print("[dry-run] not committing"); return 0

    run_git("add",
            f"pieces/{target_id}/movements/m{n}",
            f"pieces/{target_id}/movements.json",
            f"pieces/{target_id}/play.html",
            "scripts/theatrical_log.jsonl")
    msg = f"theatrical: {target_id} → m{n} · {word}"
    run_git("commit", "-m", msg)
    if not args.no_push:
        push = run_git("push", check=False)
        if push.returncode != 0:
            print(f"push failed: {push.stderr}"); return 6
    print(f"committed: {msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
