#!/usr/bin/env python3
"""Single-tick mutation worker for particle_art.

Pipeline:
  1. Load lineage.json + preferences.json.
  2. Pick a parent piece (favoring user-marked favorites; uniform if none).
  3. Sample a mutation directive from mutation_directives.json.
  4. Build the prompt: parent HTML + meta + directive.
  5. Call Claude (subscription primary, Bedrock fallback).
  6. Extract <html>…</html>; validate (parses, has script, references three).
  7. Write pieces/<new_id>/index.html + meta.json; append to lineage.json.
  8. Commit + push (so Vercel auto-deploys, GitHub Actions auto-renders thumb).
  9. Record spend; bail early if over budget.

Designed for cron (no interactive prompts). All paths relative to repo root.
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

LINEAGE = REPO / "lineage.json"
PIECES = REPO / "pieces"
PREFS = REPO / "scripts" / "preferences.json"
DIRECTIVES = REPO / "scripts" / "mutation_directives.json"
LOG = REPO / "scripts" / "mutation_log.jsonl"
PENDING_QUEUE = REPO / "scripts" / "pending_directives.jsonl"


def load_json(p: Path) -> dict:
    return json.loads(p.read_text())


def _pop_pending_directive() -> tuple[str, str] | None:
    """Pop the first valid directive from pending_directives.jsonl.

    Handles both 'directive' (pinterest_agent) and 'priority_directive'
    (pinterest_critic_agent) field names. Returns (directive_text, source)
    or None if the queue is empty.
    """
    if not PENDING_QUEUE.exists():
        return None
    lines = [l for l in PENDING_QUEUE.read_text().splitlines() if l.strip()]
    for i, line in enumerate(lines):
        try:
            entry = json.loads(line)
        except Exception:
            continue
        directive = entry.get("priority_directive") or entry.get("directive", "")
        if not directive:
            continue
        remaining = [l for j, l in enumerate(lines) if j != i]
        PENDING_QUEUE.write_text(("\n".join(remaining) + "\n") if remaining else "")
        return directive, entry.get("source", "pending")
    return None


def _tags_from_directive(directive_id: str, parent: dict) -> list[str]:
    """Best-effort tag set derived from directive id + parent's existing tags/direction."""
    tags = []
    parent_tags = parent.get("tags") or []
    tags.extend(parent_tags)
    direction = parent.get("direction", "")
    for tok in re.split(r"[-_\s]+", direction):
        if tok and tok not in tags:
            tags.append(tok)
    if directive_id.startswith("channel_"):
        tags.append(directive_id.replace("channel_", "channel:"))
    if directive_id == "cross_pollinate":
        tags.append("cross-pollinated")
    if directive_id == "fog_and_light_beam":
        tags.append("volumetric-light")
    if directive_id == "multi_click_states":
        tags.append("multi-state")
    if directive_id == "swap_object_model":
        tags.append("object-cloud")
    if directive_id not in tags:
        tags.append(directive_id)
    # dedup preserving order
    seen, out = set(), []
    for t in tags:
        if t not in seen:
            out.append(t); seen.add(t)
    return out


def _read_recent_log(n: int = 50) -> list[dict]:
    """Read the last n entries from mutation_log.jsonl. Empty list if missing."""
    if not LOG.exists():
        return []
    lines = LOG.read_text().splitlines()[-n:]
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except Exception:
            pass
    return out


def pick_parent(lineage: dict, prefs: dict, recent: list[dict]) -> dict:
    """Frontier-expansion picker.

    Weight each candidate by 1 / (1 + descendants^0.7), so favorites with no
    children yet get picked first and the lineage tree fills out evenly. Then
    skip the parents of the last two ticks (when alternatives exist) so we
    don't get bursty repeats. Final pick is weighted-random over what's left.
    """
    pieces = lineage["pieces"]
    if not pieces:
        raise RuntimeError("no pieces to mutate")
    marks = prefs.get("marks", {})
    favored = [p for p in pieces if marks.get(p["id"], {}).get("favorite")]
    dropped = {pid for pid, m in marks.items() if m.get("drop")}
    pool = favored if favored else [p for p in pieces if p["id"] not in dropped]
    if not pool:
        pool = pieces

    # count descendants for each piece (count of pieces with this id anywhere in their lineage chain)
    parent_of = {p["id"]: p.get("parent_id") for p in pieces}
    desc_count: dict[str, int] = {p["id"]: 0 for p in pieces}
    for p in pieces:
        cur = p.get("parent_id")
        seen = set()
        while cur and cur not in seen:
            if cur in desc_count:
                desc_count[cur] += 1
            seen.add(cur)
            cur = parent_of.get(cur)

    # last-2 deduplication
    last_two = {r.get("parent") for r in recent[-2:] if r.get("parent")}
    candidates = [p for p in pool if p["id"] not in last_two] or pool

    # weight: 1 / (1 + descendants^0.7) → no descendants ⇒ weight 1.0; 1 ⇒ ~0.61; 4 ⇒ ~0.36; 16 ⇒ ~0.13
    weights = [1.0 / (1.0 + (desc_count.get(p["id"], 0) ** 0.7)) for p in candidates]
    return random.choices(candidates, weights=weights, k=1)[0]


def _lineage_chain(piece_id: str, pieces: list[dict]) -> list[str]:
    """Return [piece_id, parent, grandparent, ...] — root last."""
    by_id = {p["id"]: p for p in pieces}
    out, cur, seen = [], piece_id, set()
    while cur and cur not in seen:
        out.append(cur)
        seen.add(cur)
        cur = by_id.get(cur, {}).get("parent_id")
    return out


def _directives_in_lineage(piece_id: str, pieces: list[dict]) -> list[str]:
    """All directive_ids ever applied along the chain leading to piece_id (root → here)."""
    chain = _lineage_chain(piece_id, pieces)
    by_id = {p["id"]: p for p in pieces}
    out = []
    for pid in reversed(chain):
        p = by_id.get(pid, {})
        d = p.get("mutation_directive_id") or p.get("mutation_directive")
        if d and d != "seed":
            out.append(d)
    return out


def sample_directive(spec_path: Path, parent_id: str, recent: list[dict]) -> tuple[str, str]:
    spec = load_json(spec_path)
    # constraints:
    #   1) never repeat (parent_id × directive_id) — descendants of a piece never use the same directive twice
    #   2) avoid directives used in the last 3 system-wide ticks (so the gallery doesn't get bursts of one style)
    used_for_parent = {r.get("directive_id") for r in recent if r.get("parent") == parent_id}
    cooldown = {r.get("directive_id") for r in recent[-3:]}
    candidates, weights = [], []
    for d in spec["directives"]:
        did = d["id"]
        if did in used_for_parent:
            continue
        w = d["weight"]
        if did in cooldown:
            w *= 0.15  # heavy soft-penalty rather than hard exclude
        candidates.append(d); weights.append(w)
    if not candidates:                       # all used → reset for this parent
        candidates = spec["directives"]; weights = [d["weight"] for d in spec["directives"]]
    # variety-bias: under-used directives across the whole log get more weight
    weights = element_counts.bias_weights(
        candidates, weights, element_counts.directive_counts(),
        key=lambda d: d["id"], beta=1.0,
    )
    chosen = random.choices(candidates, weights=weights, k=1)[0]
    text = chosen["directive"]
    if "{" in text and "params" in chosen:
        param = random.choice(chosen["params"])
        text = re.sub(r"\{[^}]+\}", str(param), text, count=1)
    return chosen["id"], text


def _read_taste() -> dict:
    p = REPO / "taste.json"
    if not p.exists(): return {}
    try: return json.loads(p.read_text())
    except Exception: return {}


def _read_ratings() -> dict:
    p = REPO / "ratings.json"
    if not p.exists(): return {}
    try:
        d = json.loads(p.read_text())
        return d.get("ratings", {})
    except Exception:
        return {}


def _swarm_block() -> str:
    """Inject the last few swarm-debate directives. Lazy import so missing module is non-fatal."""
    try:
        from swarm_debate import advice_block
        return advice_block()
    except Exception:
        return ""


def _rejection_block() -> str:
    """Build a block describing what the user has rejected.

    Pulled from taste.json + preferences.json drops + ratings.json N-votes.
    Injected into every prompt so the LLM doesn't re-emit known-bad patterns.
    """
    taste = _read_taste()
    prefs = _read_ratings()
    lines = []
    dislikes = taste.get("dislikes", {})
    if dislikes.get("directions"):
        lines.append("Directions the user has explicitly rejected — DO NOT produce these:")
        for d in dislikes["directions"][:8]:
            lines.append(f"  - {d}")
    if dislikes.get("techniques"):
        lines.append("Technical patterns that have failed silently — AVOID:")
        for t in dislikes["techniques"][:5]:
            lines.append(f"  - {t}")
    n_voted = [k for k, v in prefs.items() if v == "n"]
    if n_voted:
        lines.append(f"Ideas the user has voted N (bad) on: {', '.join(n_voted[:12])}")
    principles = taste.get("principles") or []
    if principles:
        lines.append("Principles the user holds — every output should respect these:")
        for p in principles:
            lines.append(f"  - {p}")
    return "\n".join(lines) if lines else ""


def _iterate_when_chosen_block(parent_id: str) -> str:
    """If the user has left direction notes for this specific parent, surface them."""
    taste = _read_taste()
    notes = (taste.get("iterate_when_chosen") or {}).get(parent_id)
    if not notes:
        return ""
    return (
        f"USER ITERATION DIRECTION FOR PARENT {parent_id} — apply on this descendant:\n"
        f"  {notes}"
    )


def _seed_block(seed_dir_path: str | None) -> tuple[str, str | None]:
    """Read seeds/<slug>/meta.json + artist SKILL.md and produce a prompt block.
    Returns (prompt_text, override_directive). Both empty if no seed."""
    if not seed_dir_path:
        return "", None
    seed_dir = Path(seed_dir_path)
    if not seed_dir.is_absolute():
        seed_dir = REPO / seed_dir_path
    meta_path = seed_dir / "meta.json"
    if not meta_path.exists():
        return "", None
    try:
        m = json.loads(meta_path.read_text())
    except Exception:
        return "", None
    skill_text = ""
    skill_name = m.get("artist_skill")
    if skill_name:
        skill_path = Path.home() / ".claude/skills" / skill_name / "SKILL.md"
        if skill_path.exists():
            try:
                skill_text = skill_path.read_text()[:3500]
            except Exception:
                pass
    visual_brief = "\n".join(f"  - {b}" for b in (m.get("visual_brief") or []))
    block = (
        f"SEED-DRIVEN MUTATION — this descendant must channel a specific artist's DNA "
        f"applied to a specific visual seed.\n"
        f"Artist: {m.get('artist','?')}\n"
        f"Likely work: {m.get('likely_work','?')}\n"
        f"Visual brief from the seed image:\n{visual_brief}\n\n"
        f"--- ARTIST SKILL DNA ({skill_name}) ---\n{skill_text}\n--- end skill ---"
    )
    return block, m.get("channel_directive")


def build_prompt(parent: dict, parent_html: str, directive: str, seed_block: str = "") -> tuple[str, str]:
    rejection = _rejection_block()
    swarm = _swarm_block()
    iterate_note = _iterate_when_chosen_block(parent["id"])
    eval_note = read_top_eval_note()
    system = (
        "You are a creative-coding shader/three.js mutation engine for a particle-art "
        "evolutionary gallery. Each mutation produces ONE self-contained HTML file "
        "(three.js loaded via importmap from unpkg.com, all GLSL inline, no build step, "
        "no external CSS/JS files beyond CDN imports). The output must run by opening "
        "the file in a modern browser. Keep the file under 600 lines. Preserve a small "
        "id label fixed to the bottom-left corner, font-family ui-monospace, color #cdd2dc, "
        "opacity 0.55, font-size 11px (the 3-char id will be supplied).\n\n"
        + (("USER TASTE GUARDRAILS — read carefully:\n" + rejection + "\n\n") if rejection else "")
        + ((iterate_note + "\n\n") if iterate_note else "")
        + ((seed_block + "\n\n") if seed_block else "")
        + ((swarm + "\n\n") if swarm else "")
        + "If your output would violate any of the above, redesign before emitting. "
        "Better to produce a structurally simple piece that respects the guardrails "
        "than an ambitious piece that breaks them.\n\n"
        "MANDATORY RENDER GATE — the piece WILL be opened in a headless browser within "
        "5 seconds of generation and the screenshot must contain ≥0.5% non-background "
        "pixels or it is auto-rejected and DELETED. This means: (a) do NOT design pieces "
        "that stay blank until a peer connects / a websocket pulses / mic permission is "
        "granted / a user clicks. The default-state-with-zero-input must already be "
        "visually meaningful. (b) avoid silent shader-compile failures — never reference "
        "three.js auto-injected uniforms (fogColor/fogDensity/etc.) without #include. "
        "(c) keep the camera framed on the form (track centroid for growing forms). "
        "(d) if you fetch external resources (GLB, websocket), have a fallback geometry "
        "that paints something on load failure."
    )
    eval_prefix = (eval_note + "\n\n") if eval_note else ""
    user = f"""{eval_prefix}# Mutation request

## Parent piece metadata
```json
{json.dumps({k: v for k, v in parent.items() if k not in {"created_at"}}, indent=2)}
```

## Parent piece HTML (for reference — modify, don't replace from scratch)
```html
{parent_html}
```

## Mutation directive
{directive}

## Output
Reply with ONLY the new HTML file content. Start with `<!doctype html>`. No prose, no
markdown fences, no explanation. The id label in the bottom-left should read the literal
string `<NEW_ID>` — I will substitute the actual id after generation.
"""
    return system, user


def extract_html(text: str) -> str:
    """Pull <!doctype html>…</html> out of the response, tolerating fences."""
    # if model wrapped in ```html … ``` strip it
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


def safe_push() -> bool:
    """Push current branch; if rejected, pull --rebase -X theirs and retry once.
    Designed for parallel mutation workers + a noisy CI pushing thumbnails."""
    for attempt in (1, 2, 3):
        push = run_git("push", check=False)
        if push.returncode == 0:
            return True
        # fetch + rebase, biased toward our own commits, then retry
        run_git("fetch", "origin", check=False)
        run_git("pull", "--rebase", "-X", "theirs", "--autostash", check=False)
    return False


def append_log(entry: dict):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="generate but don't commit/push")
    ap.add_argument("--no-push", action="store_true", help="commit but don't push")
    ap.add_argument("--parent", help="force a specific parent id")
    ap.add_argument("--directive", help="force a specific directive text")
    ap.add_argument("--seed", help="path to a seeds/<slug>/ directory — injects the seed's "
                                   "visual brief, artist DNA (artist_skill SKILL.md), and "
                                   "channel_directive into the prompt; piece is parented "
                                   "off the seed's lineage if descendants exist, else from a forced parent")
    args = ap.parse_args()

    # budget gate
    ok, why = budget.can_spend(lib_claude.BEDROCK_COST_ESTIMATE_USD)
    if not ok and not args.dry_run:
        print(f"abort: budget — {why}")
        return 2

    lineage = load_json(LINEAGE)
    prefs = load_json(PREFS)

    recent = _read_recent_log(50)

    if args.parent:
        candidates = [p for p in lineage["pieces"] if p["id"] == args.parent]
        if not candidates:
            print(f"unknown parent: {args.parent}")
            return 3
        parent = candidates[0]
    else:
        parent = pick_parent(lineage, prefs, recent)

    parent_html = (PIECES / parent["id"] / "index.html").read_text()

    seed_block_text, seed_override_directive = _seed_block(args.seed)

    if args.directive:
        directive_id, directive = "manual", args.directive
    elif seed_override_directive:
        directive_id, directive = "seed_channel", seed_override_directive
    else:
        pending = _pop_pending_directive()
        if pending:
            directive, directive_id = pending[0], f"pending:{pending[1]}"
            print(f"using pending directive (source={pending[1]}): {directive[:80]}")
        else:
            directive_id, directive = sample_directive(DIRECTIVES, parent["id"], recent)

    print(f"parent: {parent['id']} ({parent['title']})")
    print(f"directive: {directive_id} — {directive[:120]}{'...' if len(directive) > 120 else ''}")
    if args.seed:
        print(f"seed: {args.seed}")

    system, user = build_prompt(parent, parent_html, directive, seed_block=seed_block_text)

    try:
        text, provider = lib_claude.call(system, user)
    except lib_claude.ProviderError as e:
        print(f"provider failure: {e}")
        return 4

    try:
        html = extract_html(text)
        validate(html)
    except Exception as e:
        print(f"output rejected: {e}")
        # save reject for inspection
        rej = REPO / "scripts" / f"reject_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.txt"
        rej.write_text(text)
        print(f"raw response saved to {rej}")
        return 5

    new_id = codes.generate(LINEAGE, n=1)[0]
    # tolerant replace — covers `<NEW_ID>` and HTML-escaped `&lt;NEW_ID&gt;`
    html = html.replace("<NEW_ID>", new_id).replace("&lt;NEW_ID&gt;", new_id)

    out_dir = PIECES / new_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(html)

    # render-content gate — blank pieces (shader compile fail, design that waits
    # for interaction/network input that never comes, misframed camera, etc.)
    # must NEVER ship to Vercel. Validate via Playwright before proceeding.
    try:
        import shutil
        from validate_render import validate as render_validate
        rc = render_validate([new_id], record_clip=False)
        if rc != 0:
            rej = REPO / "scripts" / f"reject_{new_id}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.html"
            rej.write_text(html)
            print(f"render gate: REJECTED {new_id} (saved to {rej.name})")
            shutil.rmtree(out_dir, ignore_errors=True)
            thumb = REPO / "thumbs" / f"{new_id}.png"
            if thumb.exists():
                thumb.unlink()
            return 7
    except ImportError as e:
        print(f"render gate: SKIPPED ({e}) — pip install playwright pillow numpy && playwright install chromium")
    except Exception as e:
        print(f"render gate: SKIPPED on exception ({e})")

    # aesthetic gate — reads bug.md (user's catalogue of anti-patterns) +
    # the new piece's HTML, asks Claude whether the piece exhibits any
    # documented failure mode (jcl dots-on-intestine, h97 sieve, 610 blurry,
    # LeePerrySmith head, ac2 noise nebula, tiny-subject-vast-canvas, etc.).
    # Failing pieces are REJECTED but their reusable IDEAS are extracted to
    # scripts/idea_extracts.jsonl so genius bits survive the cull.
    try:
        import shutil
        from aesthetic_gate import check as aesthetic_check
        ok, hits, ideas = aesthetic_check(html, new_id, parent, directive)
        if not ok:
            rej = REPO / "scripts" / f"aesthetic_reject_{new_id}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.html"
            rej.write_text(html)
            print(f"aesthetic gate: REJECTED {new_id} (saved to {rej.name})")
            shutil.rmtree(out_dir, ignore_errors=True)
            thumb = REPO / "thumbs" / f"{new_id}.png"
            if thumb.exists():
                thumb.unlink()
            return 8
    except ImportError as e:
        print(f"aesthetic gate: SKIPPED ({e})")
    except Exception as e:
        print(f"aesthetic gate: SKIPPED on exception ({e})")

    pieces_now = lineage["pieces"]
    parent_chain = _lineage_chain(parent["id"], pieces_now)            # parent → … → root
    parent_directives = _directives_in_lineage(parent["id"], pieces_now)
    new_meta = {
        "id": new_id,
        "title": f"mut.{directive_id}",
        "direction": parent.get("direction", ""),
        "stack": parent.get("stack", []),
        "input": parent.get("input", "unknown"),
        "particle_count": parent.get("particle_count"),
        "parent_id": parent["id"],
        "generation": parent.get("generation", 0) + 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mutation_directive": directive,
        "mutation_directive_id": directive_id,
        "provider": provider,
        # richer fields for future-iter introspection:
        "lineage_path": [new_id] + parent_chain,                       # [self, parent, grandparent, ...]
        "directives_in_lineage": parent_directives + [directive_id],   # root → here, ordered
        "tags": _tags_from_directive(directive_id, parent),
    }
    if args.seed:
        seed_slug = Path(args.seed).name
        new_meta["seed"] = seed_slug
    (out_dir / "meta.json").write_text(json.dumps(new_meta, indent=2))

    # update lineage.json
    lineage["pieces"].append({
        "id": new_id,
        "title": new_meta["title"],
        "direction": new_meta["direction"],
        "input": new_meta["input"],
        "particle_count": new_meta["particle_count"],
        "parent_id": parent["id"],
        "generation": new_meta["generation"],
        "created_at": new_meta["created_at"],
    })
    lineage.setdefault("edges", []).append({
        "from": parent["id"], "to": new_id, "directive": directive_id,
    })
    lineage["updated_at"] = datetime.now(timezone.utc).isoformat()
    LINEAGE.write_text(json.dumps(lineage, indent=2))

    # record spend (subscription = $0)
    cost = 0.0 if provider == "subscription" else lib_claude.BEDROCK_COST_ESTIMATE_USD
    budget.record(cost, provider, note=f"{parent['id']}->{new_id} ({directive_id})")
    append_log({
        "ts": new_meta["created_at"], "id": new_id, "parent": parent["id"],
        "directive_id": directive_id, "provider": provider, "cost_usd": cost,
    })

    print(f"created: pieces/{new_id}/  (parent={parent['id']}, gen={new_meta['generation']}, provider={provider})")

    if args.dry_run:
        print("[dry-run] not committing")
        return 0

    run_git("add",
            f"pieces/{new_id}",
            "lineage.json",
            "taste.json",
            "scripts/mutation_log.jsonl")
    msg = f"mutate {parent['id']} → {new_id} · {directive_id}"
    run_git("commit", "-m", msg)
    if not args.no_push:
        if not safe_push():
            print("push failed after retries")
            return 6
    print(f"committed: {msg}")

    # POST-MUTATION SWARM DEBATE — three distilled-artist personas critique the
    # new piece in one Claude call. Verdicts feed forward into the next tick's
    # prompt guardrails. Failure here is non-fatal (mutation is already shipped).
    try:
        from swarm_debate import debate
        v = debate(new_id)
        if v:
            n = len((v.get("verdicts") or {}))
            print(f"swarm debate: {n} verdicts logged for {new_id}")
    except Exception as e:
        print(f"swarm debate skipped: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
