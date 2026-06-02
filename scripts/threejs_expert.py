#!/usr/bin/env python3
"""threejs_expert.py — a three.js *technique* reviewer that debates the artisan.

Where swarm_debate.py is the AESTHETIC panel (Anadol / Stiles / Eliasson …),
this is the ENGINEERING voice: a single distilled "three.js technique engineer"
persona whose only job is to ask, for the piece in front of it, *is there a
three.js feature, example, or plugin that would make this cheaper, smoother, or
more capable?* It is grounded in three live resources fetched at review time:

  - threejs.org/examples         (the canonical example catalogue → files.json)
  - agargaro/batched-mesh-extensions  (BatchedMesh LOD / frustum-cull / sorting)
  - AxiomeCG/awesome-threejs      (the community resource index)

Two entry points, mirroring the two ways the gallery already learns:

  review(html, ctx)   IN-LOOP. Returns ONE concrete technical directive (≤40
                      words) naming a specific three.js example / feature /
                      plugin the artisan should adopt on the refine pass.
                      Called from improv_tick.py between critic-v1 and gen-v2,
                      so it improves the CURRENT piece before it deploys.
                      Also runnable post-ship (mutate.py) to feed memory.

  advice_block()      ASYNC MEMORY. Formats the last few technical directives
                      for injection into the NEXT tick's prompt, so the artisan
                      "starts working" already aware of the toolbox.

Network reality: on the cron VM the generators call lib_claude.call() (plain
text completion via `claude -p` / Bedrock) — there is no WebFetch tool there.
So we fetch the resources ourselves with stdlib urllib and inject a compact
digest. Fetches are memoised for PARTICLE_ART_THREEJS_TTL_H hours (default 6) so
the 12-min improv cron stays a polite client, and a baked fallback digest keeps
the tick alive when the VM has no network at all.

Cost: ~$0.07/review on Bedrock, $0 on subscription. Budget-gated; skips
silently (returns None) when the cap is hit so it can NEVER block a tick.
"""
import json
import os
import re
import ssl
import sys
import time
import urllib.request
import urllib.error
import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import lib_claude  # noqa: E402
import budget       # noqa: E402

ADVICE = REPO / "scripts" / "threejs_advice.jsonl"
CACHE = REPO / ".logs" / "threejs_resources_cache.json"

TTL_HOURS = float(os.environ.get("PARTICLE_ART_THREEJS_TTL_H", "6"))
FETCH_TIMEOUT_S = 8
DIGEST_MAX_CHARS = 2600

# Live resources. Each entry: (label, url, parser-key). Parsers below.
RESOURCES = [
    ("threejs_examples", "https://threejs.org/examples/files.json", "files_json"),
    ("batched_mesh_extensions",
     "https://raw.githubusercontent.com/agargaro/batched-mesh-extensions/master/README.md", "markdown"),
    ("awesome_threejs",
     "https://raw.githubusercontent.com/AxiomeCG/awesome-threejs/main/README.md", "markdown"),
]

# Example-name substrings most relevant to a particle / point-cloud / GPGPU
# gallery. Used to surface the highest-signal examples from files.json's ~600
# entries without dumping the whole catalogue into the prompt.
RELEVANT_EXAMPLE_HINTS = (
    "points", "gpgpu", "instanc", "buffergeometry", "batch", "sprite", "lines",
    "marchingcubes", "morph", "lod", "trail", "flow", "birds", "protoplanet",
    "raycast", "interactive", "compute", "particles", "sampler", "surface",
)

# Baked fallback — used only when ALL live fetches fail (offline VM). Kept
# deliberately short; the live digest replaces it whenever the network is up.
BAKED_FALLBACK = (
    "three.js toolbox (offline fallback digest):\n"
    "- Points + BufferGeometry + custom ShaderMaterial: the default for >50k particles; "
    "set gl_PointSize in the vertex shader, sizeAttenuation off for crisp points.\n"
    "- GPUComputationRenderer (examples: webgl_gpgpu_birds, webgl_gpgpu_protoplanet): "
    "advect positions/velocities in fragment shaders so the CPU never touches per-particle state.\n"
    "- InstancedMesh / BatchedMesh: one draw call for thousands of solid forms; "
    "agargaro/batched-mesh-extensions adds per-instance frustum culling, LOD and sorting on top of BatchedMesh.\n"
    "- MeshSurfaceSampler (examples/jsm/math/MeshSurfaceSampler): sample N points off any mesh surface "
    "to turn an imported model into an even point cloud.\n"
    "- LineSegments / Line2 (fat lines) for taut-thread or flow-ribbon work.\n"
    "- MarchingCubes for metaball / organic-blob isosurfaces.\n"
    "- EffectComposer + UnrealBloomPass for additive glow without per-particle halos."
)


# ---------------------------------------------------------------------------
# Live resource fetching (stdlib only, memoised, fail-soft)
# ---------------------------------------------------------------------------

def _ssl_contexts() -> list:
    """Ordered SSL contexts to try: system default → certifi → unverified.

    Environments vary: macOS system Python often lacks root certs (default
    fails), the VM venv usually has certifi. Unverified is the last resort —
    acceptable here because every fetched URL is a public, read-only resource
    and its text is only used as advisory prompt context (filtered downstream
    by the critic, precheck and render gate), never executed.
    """
    ctxs = [None]
    try:
        import certifi
        ctxs.append(ssl.create_default_context(cafile=certifi.where()))
    except Exception:
        pass
    ctxs.append(ssl._create_unverified_context())
    return ctxs


def _http_get(url: str) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": "particle_art-threejs-expert/1.0"})
    for ctx in _ssl_contexts():
        try:
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S, context=ctx) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError:
            return None  # 404/403 etc. — a different context won't help
        except urllib.error.URLError as e:
            # urllib wraps SSL cert failures in URLError(reason=SSLError); on
            # those, retry with the next, more permissive context. Any other
            # URLError (DNS, offline, refused) is terminal for this URL.
            if isinstance(getattr(e, "reason", None), ssl.SSLError):
                continue
            return None
        except (ssl.SSLError, TimeoutError, OSError, ValueError):
            continue
    return None


def _digest_files_json(raw: str) -> str:
    """three.js examples manifest → category counts + relevant example names."""
    try:
        data = json.loads(raw)
    except Exception:
        return ""
    cats = {k: (v if isinstance(v, list) else []) for k, v in data.items()}
    total = sum(len(v) for v in cats.values())
    cat_line = ", ".join(f"{k}({len(v)})" for k, v in cats.items())
    relevant = []
    for names in cats.values():
        for n in names:
            low = n.lower()
            if any(h in low for h in RELEVANT_EXAMPLE_HINTS):
                relevant.append(n)
    # de-dupe, keep order, cap
    seen, picks = set(), []
    for n in relevant:
        if n not in seen:
            seen.add(n); picks.append(n)
        if len(picks) >= 28:
            break
    return (
        f"threejs.org/examples — {total} official examples across: {cat_line}.\n"
        f"Particle/point-cloud/GPGPU-relevant examples (open as threejs.org/examples/#<name>):\n  "
        + "; ".join(picks)
    )


def _digest_markdown(label: str, raw: str) -> str:
    """Pull the headline + section headers + first sentence from a README."""
    lines = raw.splitlines()
    title = next((ln.strip("# ").strip() for ln in lines if ln.startswith("# ")), label)
    headers = [ln.strip("# ").strip() for ln in lines if re.match(r"^#{2,3}\s", ln)]
    # first non-empty paragraph that is prose (skip headings, badges, raw HTML)
    blurb = ""
    for ln in lines:
        s = ln.strip()
        if not s or s.startswith(("#", "![", "[![", "<", "|", "-", "*", ">")):
            continue
        s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)   # strip md links
        s = re.sub(r"<[^>]+>", "", s).strip()             # strip inline HTML tags
        if len(s) < 15:
            continue
        blurb = s
        break
    head_str = "; ".join(h for h in headers[:12] if h)
    return f"{label} ({title}): {blurb[:280]}\n  sections: {head_str}"


def _build_digest() -> str:
    parts, got_any = [], False
    for label, url, kind in RESOURCES:
        raw = _http_get(url)
        if not raw:
            continue
        if kind == "files_json":
            piece = _digest_files_json(raw)
        else:
            piece = _digest_markdown(label, raw)
        if piece:
            parts.append(piece)
            got_any = True
    if not got_any:
        return BAKED_FALLBACK
    digest = "\n\n".join(parts)
    return digest[:DIGEST_MAX_CHARS]


def resource_digest(force: bool = False) -> str:
    """Memoised live digest. Re-fetches only after TTL expires."""
    if not force and CACHE.exists():
        try:
            cached = json.loads(CACHE.read_text())
            age_h = (time.time() - cached.get("fetched_at", 0)) / 3600.0
            if age_h < TTL_HOURS and cached.get("digest"):
                return cached["digest"]
        except Exception:
            pass
    digest = _build_digest()
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps({"fetched_at": time.time(), "digest": digest}))
    except Exception:
        pass
    return digest


# ---------------------------------------------------------------------------
# In-loop review
# ---------------------------------------------------------------------------

def review(html: str, ctx: dict | None = None) -> dict | None:
    """Debate the artisan on technique. Returns a dict or None (budget/provider).

    ctx may carry: piece_id, title, direction, mode, word, parent_id.
    Returns: {"directive": str, "tools": [str], "rationale": str, "raw": str}
    """
    ctx = ctx or {}
    ok, _why = budget.can_spend(lib_claude.BEDROCK_COST_ESTIMATE_USD)
    if not ok:
        return None

    digest = resource_digest()
    html_excerpt = html[:6000] + ("\n... [truncated] ..." if len(html) > 6000 else "")

    system = (
        "You are the three.js TECHNIQUE ENGINEER on a particle-art studio team. A separate "
        "aesthetic panel judges taste; you do NOT. Your sole question: given what this piece "
        "is trying to do, is there a three.js feature, official example, or plugin that would "
        "make it cheaper, smoother, sharper, or capable of a form it currently fakes?\n\n"
        "Ground your advice ONLY in the live three.js resources below — name a concrete example "
        "slug, class, or plugin the artisan can act on. Never invent APIs.\n\n"
        "=== LIVE three.js RESOURCES (fetched this session) ===\n"
        + digest
        + "\n=== END RESOURCES ===\n\n"
        "Rules:\n"
        "- Suggest at most ONE primary change — the highest-leverage one. Concrete, not generic.\n"
        "- Prefer GPU/instanced/batched paths when the piece pushes many primitives on the CPU.\n"
        "- If the current code is already idiomatic and well-chosen, SAY SO (empty directive) "
        "rather than inventing busywork.\n"
        "- The directive must be applyable on a single refine pass; keep WebGL plumbing intact.\n"
        "Return STRICT JSON only:\n"
        '{"directive": "<≤40 words, concrete; or empty string if no change needed>", '
        '"tools": ["<example slug / class / plugin>", ...], '
        '"rationale": "<≤30 words why>"}'
    )
    user = (
        f"# Piece: {ctx.get('piece_id','(unnamed)')}\n"
        f"## Title: {ctx.get('title','')}\n"
        f"## Direction: {ctx.get('direction','')}\n"
        f"## Mode/word: {ctx.get('mode','')} / {ctx.get('word','')}\n\n"
        f"## Code excerpt (HTML):\n```html\n{html_excerpt}\n```"
    )

    try:
        text, provider = lib_claude.call(system, user)
    except lib_claude.ProviderError:
        return None

    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        parsed = json.loads(m.group(0))
    except Exception:
        return None

    directive = str(parsed.get("directive", "")).strip()
    tools = parsed.get("tools") or []
    if not isinstance(tools, list):
        tools = [str(tools)]
    tools = [str(t).strip() for t in tools if str(t).strip()][:5]
    rationale = str(parsed.get("rationale", "")).strip()[:200]

    cost = 0.0 if provider == "subscription" else lib_claude.BEDROCK_COST_ESTIMATE_USD
    budget.record(cost, provider, note=f"threejs-expert {ctx.get('piece_id','?')}")

    entry = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "piece": ctx.get("piece_id", ""),
        "provider": provider,
        "cost_usd": cost,
        "directive": directive,
        "tools": tools,
        "rationale": rationale,
    }
    if directive:  # only log actionable advice
        ADVICE.parent.mkdir(parents=True, exist_ok=True)
        with ADVICE.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    return entry


# ---------------------------------------------------------------------------
# Async memory — inject recent technical directives into the next prompt
# ---------------------------------------------------------------------------

def recent_advice(n: int = 6) -> list[dict]:
    if not ADVICE.exists():
        return []
    out = []
    for ln in ADVICE.read_text().splitlines()[-n:]:
        try:
            out.append(json.loads(ln))
        except Exception:
            pass
    return out


def advice_block(n: int = 6) -> str:
    """Format recent three.js technical directives for prompt injection."""
    recent = [r for r in recent_advice(n) if r.get("directive")]
    if not recent:
        return ""
    lines = ["RECENT three.js TECHNIQUE NOTES (engineering advice — apply when it fits the form):"]
    for r in recent:
        tools = (", ".join(r.get("tools", []))) if r.get("tools") else ""
        suffix = f"  [{tools}]" if tools else ""
        lines.append(f"  - {r['directive']}{suffix}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _self_test() -> int:
    print("── fetching live resource digest ──")
    digest = resource_digest(force=True)
    print(digest)
    print("\n── running a synthetic review ──")
    sample = (
        "<!doctype html><html><head><script type=importmap>{}</script></head><body>"
        "<script type=module>import * as THREE from 'three';"
        "const N=80000; const geo=new THREE.BufferGeometry();"
        "// 80k cubes pushed one Mesh each in a for-loop\n"
        "for(let i=0;i<N;i++){const m=new THREE.Mesh(new THREE.BoxGeometry(),mat);scene.add(m);}"
        "</script></body></html>"
    )
    res = review(sample, {"piece_id": "selftest", "title": "80k cubes", "mode": "chain", "word": "swarm"})
    print(json.dumps(res, indent=2) if res else "(no review — budget/provider unavailable)")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(_self_test())
    if len(sys.argv) < 2:
        print("usage: threejs_expert.py <piece_id> | --self-test", file=sys.stderr)
        sys.exit(2)
    pid = sys.argv[1]
    html_path = REPO / "pieces" / pid / "index.html"
    meta_path = REPO / "pieces" / pid / "meta.json"
    if not html_path.exists():
        print(f"no such piece: {pid}", file=sys.stderr)
        sys.exit(1)
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    res = review(html_path.read_text(), {
        "piece_id": pid, "title": meta.get("title", ""),
        "direction": meta.get("direction", ""),
        "mode": meta.get("improv_mode", ""), "word": meta.get("improv_word", ""),
    })
    if not res:
        print("no review emitted (budget exhausted or provider failure)", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(res, indent=2))
