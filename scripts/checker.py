#!/usr/bin/env python3
"""checker.py — Independent quality sentinel for particle_art.

Detects AI-slop patterns across all agent outputs. Runs on its own
daily schedule (04:30 UTC). Never blocks the pipeline; it only reads
agent artifacts and writes corrective directives back.

Slop checks (each independently try/excepted):
  1. code_drift      — piece barely changes from parent (token overlap)
  2. directive_loop  — same directive_id recycled across recent window
  3. score_decay     — improv critic scores trending downward
  4. chain_stagnation — deep chains locked on the same direction keywords
  5. visual_monotony — VLM judges last 6 thumbnails as visually similar

Interventions: appends priority_directives to pending_directives.jsonl
tagged source="checker". Audit trail in checker_report.jsonl.
Sends Telegram alert when ≥2 checks flag simultaneously.
"""
import base64, json, os, re, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT    = Path(__file__).parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import lib_claude as lc

LINEAGE_FILE  = ROOT / "lineage.json"
MUTATION_LOG  = SCRIPTS / "mutation_log.jsonl"
IMPROV_LOG    = SCRIPTS / "improv_log.jsonl"
PENDING       = SCRIPTS / "pending_directives.jsonl"
CHECKER_RPT   = SCRIPTS / "checker_report.jsonl"
TASTE_FILE    = SCRIPTS / "taste.json"
THUMBS_DIR    = ROOT / "thumbs"
PIECES_DIR    = ROOT / "pieces"

RECENT_N          = 20    # pieces per check window
DIRECTIVE_WINDOW  = 20    # combined log entries for directive-loop check
SCORE_WINDOW      = 15    # improv log entries for decay check
CHAIN_DEPTH_WARN  = 6     # flag chains at or beyond this generation
CODE_OVERLAP_WARN = 0.72  # jaccard threshold that triggers drift suspicion


# ── I/O helpers ───────────────────────────────────────────────────────────

def _now():
    return datetime.now(timezone.utc).isoformat()

def load_jsonl(path, last_n=None):
    lines = []
    try:
        with open(path) as f:
            for line in f:
                s = line.strip()
                if s:
                    try: lines.append(json.loads(s))
                    except: pass
    except FileNotFoundError:
        pass
    return lines[-last_n:] if last_n else lines

def load_lineage():
    with open(LINEAGE_FILE) as f:
        return json.load(f)

def load_taste():
    try:
        with open(TASTE_FILE) as f: return json.load(f)
    except: return {}

def piece_html(pid):
    try: return (PIECES_DIR / pid / "index.html").read_text()
    except: return ""

def write_directive(text, reason, **extra):
    entry = {"source": "checker", "priority_directive": text,
             "reason": reason, "queued_at": _now(), **extra}
    with open(PENDING, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry

def write_report(check, status, detail, interventions=None):
    entry = {"check": check, "status": status, "detail": detail,
             "interventions": interventions or [], "checked_at": _now()}
    with open(CHECKER_RPT, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"[checker:{check}] {status} — {detail}")
    return entry

def telegram_alert(msg):
    import requests
    token   = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("ALLOWED_CHAT_ID")
    if not token or not chat_id:
        print(f"[checker] no Telegram env vars; alert skipped")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        print(f"[checker] telegram_alert failed: {e}")


# ── token overlap helpers (code_drift) ────────────────────────────────────

def tokenize(html):
    toks = set(re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]{3,}\b', html))
    nums = set(re.findall(r'\b\d+\.\d+\b', html))
    return toks | nums

def jaccard(a, b):
    if not a or not b: return 0.0
    return len(a & b) / len(a | b)


# ── CHECK 1: code_drift ───────────────────────────────────────────────────

def check_code_drift(lineage):
    """Flag pieces whose token overlap with parent is suspiciously high."""
    pieces = lineage["pieces"]
    by_id  = {p["id"]: p for p in pieces}
    recent = [p for p in pieces if p.get("parent_id")][-RECENT_N:]

    high_overlap = []
    for p in recent:
        pid = p.get("parent_id")
        if not pid or pid not in by_id: continue
        ch = piece_html(p["id"]); pa = piece_html(pid)
        if not ch or not pa: continue
        ratio = len(ch) / max(len(pa), 1)
        if ratio < 0.5 or ratio > 2.2: continue   # dramatic size change = genuine rewrite
        ov = jaccard(tokenize(ch), tokenize(pa))
        if ov > CODE_OVERLAP_WARN:
            high_overlap.append({"id": p["id"], "parent": pid, "ov": round(ov, 3)})

    if not high_overlap:
        write_report("code_drift", "ok", f"0/{len(recent)} pieces flagged for high overlap")
        return []

    # Ask Claude to judge the top 3 suspicious pairs
    pairs_text = []
    for item in high_overlap[:3]:
        ch_ex = piece_html(item["id"])[:2500]
        pa_ex = piece_html(item["parent"])[:2500]
        pairs_text.append(
            f"=== {item['id']} (overlap={item['ov']}) vs parent {item['parent']} ===\n"
            f"PARENT:\n```html\n{pa_ex}\n```\nCHILD:\n```html\n{ch_ex}\n```"
        )
    judgment, _ = lc.call(
        "Code-diversity auditor for a generative art system. Return valid JSON only.",
        "\n\n".join(pairs_text) + "\n\n"
        "For each piece: is the child meaningfully different in visual/algorithmic terms, "
        "or is it copy-paste with cosmetic renaming? "
        'Return: [{"id":"...","verdict":"copy"|"genuine","reason":"..."}]'
    )
    try:
        verdicts = json.loads(judgment.strip())
    except:
        verdicts = []

    copies = [v for v in verdicts if isinstance(v, dict) and v.get("verdict") == "copy"]
    interventions = []
    for v in copies:
        d = (f"Piece {v['id']} was copy-paste slop — child barely differed from parent. "
             f"Abandon the parent rendering pipeline entirely: new attractor equation, "
             f"different geometry primitive, rewritten fragment shader. "
             f"Reason: {v.get('reason', '')}")
        interventions.append(write_directive(d, reason="code_drift",
                                             flagged_piece=v["id"]))

    status = "flag" if copies else "warn"
    write_report("code_drift", status,
                 f"{len(copies)} copy-paste / {len(high_overlap)} high-overlap in {len(recent)} recent",
                 interventions)
    return interventions


# ── CHECK 2: directive_loop ───────────────────────────────────────────────

def check_directive_loop():
    """Flag if any directive_id dominates ≥28% of the recent combined log window."""
    mut   = load_jsonl(MUTATION_LOG, last_n=DIRECTIVE_WINDOW)
    improv = load_jsonl(IMPROV_LOG,  last_n=DIRECTIVE_WINDOW)
    combined = sorted(mut + improv, key=lambda e: e.get("ts", ""))[-DIRECTIVE_WINDOW:]

    directives = [e.get("directive_id", "") for e in combined if e.get("directive_id")]
    if len(directives) < 5:
        write_report("directive_loop", "ok", "Insufficient log entries; skipping")
        return []

    counter   = Counter(directives)
    top       = counter.most_common(3)
    thresh    = max(3, int(len(directives) * 0.28))
    recycled  = [(d, c) for d, c in top if c >= thresh]

    if not recycled:
        write_report("directive_loop", "ok", f"Distribution healthy — top: {top[:3]}")
        return []

    interventions = []
    for overused, count in recycled:
        pct = round(100 * count / len(directives))
        breaker, _ = lc.call(
            "Generative particle-art mutation expert. "
            "Respond with only the directive text, no quotes, no prefix, ≤25 words.",
            f"The directive '{overused}' appeared in {pct}% of recent pieces — clear creative fatigue. "
            f"Write ONE specific counter-directive that completely breaks away from it: "
            f"foreign technique, opposite material logic, different geometry family."
        )
        breaker = breaker.strip().strip("\"'")
        interventions.append(write_directive(
            breaker,
            reason=f"directive_loop: '{overused}' {pct}% of last {len(directives)}",
            overused_directive=overused, count=count
        ))

    write_report("directive_loop", "flag", f"Recycled: {recycled}", interventions)
    return interventions


# ── CHECK 3: score_decay ──────────────────────────────────────────────────

def check_score_decay():
    """Flag if improv combined critic scores are trending down over SCORE_WINDOW entries."""
    entries = load_jsonl(IMPROV_LOG, last_n=SCORE_WINDOW * 2)
    scored  = [e for e in entries
               if e.get("critic", {}).get("v1", {}).get("combined") is not None]
    if len(scored) < 6:
        write_report("score_decay", "ok", "Too few scored improv entries; skipping")
        return []

    scores     = [e["critic"]["v1"]["combined"] for e in scored[-SCORE_WINDOW:]]
    mid        = len(scores) // 2
    avg_first  = sum(scores[:mid])  / mid
    avg_second = sum(scores[mid:])  / (len(scores) - mid)
    delta      = avg_second - avg_first

    if delta > -0.8:
        write_report("score_decay", "ok",
                     f"Scores stable: {avg_first:.1f}→{avg_second:.1f} (Δ{delta:+.1f})")
        return []

    taste    = load_taste()
    dislikes = "; ".join((taste.get("dislikes") or {}).get("techniques", [])[:5])
    raw, _   = lc.call(
        "Generative art quality consultant. Return a JSON array of exactly 2 strings, no prose.",
        f"Improv scores declined from {avg_first:.1f} to {avg_second:.1f} over {SCORE_WINDOW} pieces. "
        f"Known aesthetic dislikes: {dislikes}. "
        f"Write 2 specific (<25 words each) mutation directives to break out of this creative rut. "
        f'Return: ["directive one", "directive two"]'
    )
    try:
        new_dirs = json.loads(raw.strip())
        if not isinstance(new_dirs, list): new_dirs = [raw.strip()]
    except:
        new_dirs = [raw.strip()]

    interventions = []
    for d in new_dirs[:2]:
        d = str(d).strip().strip("\"'")
        interventions.append(write_directive(
            d, reason=f"score_decay: Δ{delta:+.1f} over {SCORE_WINDOW} improv ticks"
        ))

    write_report("score_decay", "flag",
                 f"Scores {avg_first:.1f}→{avg_second:.1f} (Δ{delta:+.1f})",
                 interventions)
    return interventions


# ── CHECK 4: chain_stagnation ─────────────────────────────────────────────

def check_chain_stagnation(lineage):
    """Flag deep chains where the same direction keyword repeats through the ancestry."""
    pieces = lineage["pieces"]
    by_id  = {p["id"]: p for p in pieces}

    def ancestors(pid):
        chain, seen = [], set()
        cur = pid
        while cur and cur not in seen:
            n = by_id.get(cur)
            if not n: break
            chain.append(n); seen.add(cur)
            cur = n.get("parent_id")
        return chain

    recent  = pieces[-RECENT_N:]
    flagged = []
    for p in recent:
        if p.get("generation", 0) < CHAIN_DEPTH_WARN: continue
        chain = ancestors(p["id"])[:CHAIN_DEPTH_WARN]
        if len(chain) < CHAIN_DEPTH_WARN: continue
        words = []
        for n in chain:
            words.extend(re.findall(r'\b\w{5,}\b', (n.get("direction") or "").lower()))
        top = Counter(words).most_common(1)
        if top and top[0][1] >= CHAIN_DEPTH_WARN - 1:
            flagged.append({"id": p["id"], "gen": p.get("generation", 0),
                            "stuck_on": top[0][0]})

    if not flagged:
        write_report("chain_stagnation", "ok",
                     f"No stagnant chains in last {RECENT_N} pieces")
        return []

    interventions = []
    for item in flagged[:2]:
        chain = ancestors(item["id"])
        alt = next((n["id"] for n in reversed(chain)
                    if n.get("generation", 99) <= 2), None)
        d = (f"Chain {item['id']} (gen {item['gen']}) is locked on '{item['stuck_on']}'. "
             f"Force a cross-lineage break: use a gen-1/2 ancestor as parent and "
             f"introduce a completely different field topology or attractor family.")
        interventions.append(write_directive(
            d, reason="chain_stagnation",
            stagnant_piece=item["id"], suggested_ancestor=alt
        ))

    write_report("chain_stagnation", "flag",
                 f"{len(flagged)} stagnant chains: {[f['id'] for f in flagged]}",
                 interventions)
    return interventions


# ── CHECK 5: visual_monotony ──────────────────────────────────────────────

def check_visual_monotony(lineage):
    """Composite last 6 thumbnails into a grid and ask Bedrock VLM if they look the same."""
    pieces = lineage["pieces"]
    recent = [p["id"] for p in pieces[-10:]]
    avail  = [(pid, THUMBS_DIR / f"{pid}.png")
              for pid in recent if (THUMBS_DIR / f"{pid}.png").exists()]

    if len(avail) < 4:
        write_report("visual_monotony", "ok",
                     f"Only {len(avail)} thumbnails available; skipping VLM check")
        return []

    try:
        from PIL import Image as PILImage
        import io
        imgs = [(pid, PILImage.open(p).convert("RGB").resize((200, 125)))
                for pid, p in avail[:6]]
        cols = min(3, len(imgs))
        rows = (len(imgs) + cols - 1) // cols
        grid = PILImage.new("RGB", (cols * 200, rows * 125), (10, 10, 13))
        for i, (_, im) in enumerate(imgs):
            grid.paste(im, ((i % cols) * 200, (i // cols) * 125))
        buf = io.BytesIO()
        grid.save(buf, format="JPEG", quality=82)
        b64 = base64.standard_b64encode(buf.getvalue()).decode()
    except ImportError:
        write_report("visual_monotony", "ok", "PIL unavailable; VLM grid check skipped")
        return []
    except Exception as e:
        write_report("visual_monotony", "ok", f"Grid build failed: {e}")
        return []

    ids_str = ", ".join(pid for pid, _ in imgs)
    try:
        raw = lc.call_bedrock_vision(
            "Art-diversity auditor for a generative particle-art system.",
            f"These are the {len(imgs)} most-recent pieces ({ids_str}) arranged in a grid. "
            f"Are they visually monotonous — sharing the same palette, density, "
            f"composition, or motion pattern? Or genuinely diverse? "
            f'Return JSON: {{"verdict":"monotonous"|"diverse","reason":"...","dominant_trait":"..."}}',
            b64, media_type="image/jpeg", max_tokens=256
        )
        result = json.loads(raw.strip())
    except Exception as e:
        write_report("visual_monotony", "ok", f"VLM call failed: {e}")
        return []

    if result.get("verdict") != "monotonous":
        write_report("visual_monotony", "ok",
                     f"Visually diverse: {result.get('reason', '')}")
        return []

    dominant = result.get("dominant_trait", "repetitive visual pattern")
    d = (f"Visual monotony alert — recent pieces all share '{dominant}'. "
         f"Next piece must invert this: dark/dense→sparse/bright; "
         f"organic→hard geometric; static→violently kinetic. "
         f"Make it look nothing like the last 6 pieces.")
    entry = write_directive(d, reason=f"visual_monotony: {dominant}")

    write_report("visual_monotony", "flag",
                 f"Monotonous ({dominant}): {result.get('reason', '')}",
                 [entry])
    return [entry]


# ── main ──────────────────────────────────────────────────────────────────

def main():
    print(f"[checker] starting {_now()}")
    lineage = load_lineage()

    checks = [
        ("code_drift",       lambda: check_code_drift(lineage)),
        ("directive_loop",   check_directive_loop),
        ("score_decay",      check_score_decay),
        ("chain_stagnation", lambda: check_chain_stagnation(lineage)),
        ("visual_monotony",  lambda: check_visual_monotony(lineage)),
    ]

    all_interventions = []
    flag_count        = 0
    for name, fn in checks:
        try:
            ivs = fn()
            all_interventions.extend(ivs)
            if ivs: flag_count += 1
        except Exception as e:
            print(f"[checker:{name}] ERROR: {e}")
            write_report(name, "error", str(e))

    print(f"[checker] done — {flag_count}/5 checks flagged, "
          f"{len(all_interventions)} directives queued")

    if flag_count >= 2:
        telegram_alert(
            f"🔍 *particle\\_art checker*: {flag_count}/5 slop checks flagged → "
            f"{len(all_interventions)} corrective directives injected"
        )


if __name__ == "__main__":
    main()
