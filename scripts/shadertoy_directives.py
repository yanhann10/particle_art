"""Shadertoy SOTW directive injector.
Usage:
  python3 scripts/shadertoy_directives.py            # API if SHADERTOY_KEY set
  python3 scripts/shadertoy_directives.py --demo     # hardcoded IDs
  python3 scripts/shadertoy_directives.py --dry-run  # print only
"""
import argparse, json, os, sys, urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
REPO = HERE.parent
PENDING    = HERE / "pending_directives.jsonl"
SHADER_LIB = HERE / "shader_lib"
SEEN_FILE  = HERE / "knowledge" / "sotw_seen.txt"
FALLBACK_IDS = ["XlSSzK", "WdBfDd", "NtlSDs"]
SYSTEM = (
    "You are a particle art mutation director. Given a fragment of a Shadertoy "
    "award-winning shader and a parent piece ID, write ONE mutation directive "
    "(max 2 sentences, imperative) that transplants the key technique from the "
    "Shadertoy into the parent piece. Be specific about the technique — name the "
    "algorithm, the visual effect, and how to apply it."
)

def get_shader_ids(demo):
    key = os.environ.get("SHADERTOY_KEY", "")
    if demo or not key:
        return FALLBACK_IDS
    try:
        with urllib.request.urlopen(
            f"https://www.shadertoy.com/api/v1/shaders?sort=popular&num=5&key={key}", timeout=10
        ) as r:
            return [s["id"] for s in json.loads(r.read()).get("Results", [])][:5]
    except Exception as e:
        print(f"[warn] API list failed ({e}); using fallback", file=sys.stderr)
        return FALLBACK_IDS

def load_glsl(sid):
    cached = SHADER_LIB / f"sotw_{sid}.glsl"
    if cached.exists():
        return cached.read_text()
    key = os.environ.get("SHADERTOY_KEY", "")
    if not key:
        return f"// no SHADERTOY_KEY; id={sid}"
    try:
        with urllib.request.urlopen(
            f"https://www.shadertoy.com/api/v1/shaders/{sid}?key={key}", timeout=10
        ) as r:
            passes = json.loads(r.read()).get("Shader", {}).get("renderpass", [])
        return "\n\n".join(p.get("code", "") for p in passes)
    except Exception as e:
        return f"// fetch failed ({e}); id={sid}"

def pick_parents():
    try:
        marks = json.loads((HERE / "preferences.json").read_text()).get("marks", {})
        favs = [k for k, v in marks.items() if v.get("favorite")]
        if favs:
            return favs[-3:]
    except Exception:
        pass
    pieces = sorted(p.name for p in (REPO / "pieces").iterdir() if p.is_dir())
    return pieces[:3] or ["unknown"]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo",    action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    sys.path.insert(0, str(HERE))
    from lib_claude import call

    SEEN_FILE.parent.mkdir(exist_ok=True)
    seen    = set(SEEN_FILE.read_text().split()) if SEEN_FILE.exists() else set()
    new_ids = [s for s in get_shader_ids(args.demo) if s not in seen]
    parents = pick_parents()

    if not new_ids:
        print("[shadertoy_directives] all shaders already seen — nothing to do")
        return

    entries = []
    for i, sid in enumerate(new_ids):
        parent = parents[i % len(parents)]
        glsl   = load_glsl(sid)[:1500]
        directive, provider = call(SYSTEM, f"SHADERTOY TECHNIQUE:\n{glsl}\n\nPARENT PIECE ID: {parent}")
        directive = directive.strip()
        entries.append({"source": "shadertoy_sotw", "shader_id": sid, "parent_id": parent,
                         "priority_directive": directive,
                         "queued_at": datetime.now(timezone.utc).isoformat()})
        print(f"[{provider}] {sid} → {parent}: {directive[:80]}…")

    if args.dry_run:
        print("\n--- DRY RUN: would append ---")
        for e in entries:
            print(json.dumps(e))
        return

    with PENDING.open("a") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    with SEEN_FILE.open("a") as f:
        for sid in new_ids:
            f.write(sid + "\n")
    print(f"[shadertoy_directives] appended {len(entries)} directive(s)")


if __name__ == "__main__":
    main()
