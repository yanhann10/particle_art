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
import lib_claude

LINEAGE = REPO / "lineage.json"
PIECES = REPO / "pieces"
PREFS = REPO / "scripts" / "preferences.json"
DIRECTIVES = REPO / "scripts" / "mutation_directives.json"
LOG = REPO / "scripts" / "mutation_log.jsonl"


def load_json(p: Path) -> dict:
    return json.loads(p.read_text())


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
    pieces = lineage["pieces"]
    if not pieces:
        raise RuntimeError("no pieces to mutate")
    marks = prefs.get("marks", {})
    favored = [p for p in pieces if marks.get(p["id"], {}).get("favorite")]
    dropped = {pid for pid, m in marks.items() if m.get("drop")}
    pool = favored if favored else [p for p in pieces if p["id"] not in dropped]
    if not pool:
        pool = pieces
    # avoid picking the parent of either of the last two ticks if we have alternatives
    last_two = {r.get("parent") for r in recent[-2:] if r.get("parent")}
    deduped = [p for p in pool if p["id"] not in last_two]
    return random.choice(deduped if deduped else pool)


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
    chosen = random.choices(candidates, weights=weights, k=1)[0]
    text = chosen["directive"]
    if "{" in text and "params" in chosen:
        param = random.choice(chosen["params"])
        text = re.sub(r"\{[^}]+\}", str(param), text, count=1)
    return chosen["id"], text


def build_prompt(parent: dict, parent_html: str, directive: str) -> tuple[str, str]:
    system = (
        "You are a creative-coding shader/three.js mutation engine for a particle-art "
        "evolutionary gallery. Each mutation produces ONE self-contained HTML file "
        "(three.js loaded via importmap from unpkg.com, all GLSL inline, no build step, "
        "no external CSS/JS files beyond CDN imports). The output must run by opening "
        "the file in a modern browser. Keep the file under 600 lines. Preserve a small "
        "id label fixed to the bottom-left corner, font-family ui-monospace, color #cdd2dc, "
        "opacity 0.55, font-size 11px (the 3-char id will be supplied)."
    )
    user = f"""# Mutation request

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

    if args.directive:
        directive_id, directive = "manual", args.directive
    else:
        directive_id, directive = sample_directive(DIRECTIVES, parent["id"], recent)

    print(f"parent: {parent['id']} ({parent['title']})")
    print(f"directive: {directive_id} — {directive}")

    system, user = build_prompt(parent, parent_html, directive)

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
    html = html.replace("<NEW_ID>", new_id)

    out_dir = PIECES / new_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(html)

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
    }
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
            "scripts/mutation_log.jsonl")
    msg = f"mutate {parent['id']} → {new_id} · {directive_id}"
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
