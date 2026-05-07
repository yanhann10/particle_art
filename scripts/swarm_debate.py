#!/usr/bin/env python3
"""Per-piece swarm debate — three distilled-artist personas critique a fresh
mutation in ONE Claude call (cheaper than three separate calls). Writes the
structured response to scripts/swarm_advice.jsonl. The next tick's prompt
reads the last few entries and injects them as 'swarm panel said' guardrails.

Personas (in priority order, fall through to inline if a SKILL.md is missing):
  - Refik Anadol (data-as-material, scale, cyan-violet duotone, ML-flow)
  - Sasha Stiles (language-as-form, human-AI poetics, Cursive Binary)
  - Entangled Others / Sofia Crespo + F. McCormick (biology-as-substrate)

Cost: ~$0.07/call. Run only after the mutation succeeds.
"""
import json, sys, pathlib, datetime, re
REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import lib_claude  # noqa
import budget       # noqa

ADVICE = REPO / "scripts" / "swarm_advice.jsonl"

PERSONAS = [
    ("refik_anadol",
     "Refik Anadol — data as material, ML hallucination as paint, MoMA-scale ambition. "
     "Speaks for: scale that overwhelms (200k+ particles), cyan/violet/pearl duotone, "
     "smooth slow flow over hard edges, the architecture of memory, never stillness for its own sake."),
    ("sasha_stiles",
     "Sasha Stiles — language IS form. Cursive Binary (binary as a script, glyphs as a body). "
     "Speaks for: typography/glyphs as compositional material, human-AI co-authorship, "
     "the void around the mark, slowness that earns the next line. Will object when language is reduced to decoration."),
    ("entangled_others",
     "Entangled Others (Sofia Crespo + Feileacan McCormick) — biology AS the substrate, not metaphor. "
     "Speaks for: emergent organic forms (slime mold, neural webs, atomic gardens), AI as ecology not tool, "
     "more-than-human composition. Will object when the form is human-centered or sterile."),
    ("olafur_eliasson",
     "Olafur Eliasson — Icelandic light/atmosphere/perception artist (Studio Olafur Eliasson, Berlin). "
     "Speaks for: light AS material (sun-disc 'The Weather Project', mist + colored fog, double sunsets), "
     "perceptual phenomenology — viewer's body and the work co-produce the seeing, slow color gradients across "
     "vast volumetric space, geometric primitives (icosahedra, dodecahedra, glaciers' crystalline geometry), "
     "elemental palette of glacial ice / arctic dusk / volcanic basalt / monochrome amber-haze. Will object when "
     "the work is screen-flat (no atmosphere/depth/light), when color is decorative not phenomenological, "
     "or when the viewer is positioned as observer rather than participant."),
]


def _read_skill(name: str) -> str:
    p = pathlib.Path.home() / ".claude/skills" / name / "SKILL.md"
    if p.exists():
        try:
            txt = p.read_text()
            return txt[:3500]   # trim — first 3.5k chars hold the distilled DNA
        except Exception:
            pass
    return ""


def debate(piece_id: str) -> dict | None:
    piece_dir = REPO / "pieces" / piece_id
    html_path = piece_dir / "index.html"
    meta_path = piece_dir / "meta.json"
    if not html_path.exists() or not meta_path.exists():
        return None
    html = html_path.read_text()
    meta = json.loads(meta_path.read_text())

    # truncate HTML aggressively — the distilled artists react to the *form*, not the code
    html_excerpt = html[:6000] + ("\n... [truncated] ..." if len(html) > 6000 else "")

    # inline the persona briefs + first chunk of each SKILL.md if available
    brief_parts = []
    for label, desc in PERSONAS:
        skill_name = label.replace("_", "-") + "-skill"
        skill_text = (_read_skill(skill_name) or "")[:1500]
        brief_parts.append(f"### {label}\n{desc}\n\n{skill_text}")
    persona_briefs = "\n\n".join(brief_parts)

    system = (
        "You are facilitating a three-voice swarm debate. Read the piece description and code excerpt. "
        "Each artist persona below speaks ONCE — reading the piece in their own register, naming what works, "
        "what concerns them, and ONE concrete improvement directive (≤25 words) the worker could apply on the "
        "next iteration. Be specific to THIS piece; never generic.\n\n"
        + persona_briefs
        + "\n\nReturn STRICT JSON only:\n"
          '{"refik_anadol":   {"praise":"...", "concern":"...", "directive":"..."},\n'
          ' "sasha_stiles":   {"praise":"...", "concern":"...", "directive":"..."},\n'
          ' "entangled_others":{"praise":"...", "concern":"...", "directive":"..."},\n'
          ' "olafur_eliasson":{"praise":"...", "concern":"...", "directive":"..."}}'
    )
    user = (
        f"# Piece: {piece_id}\n"
        f"## Title: {meta.get('title','')}\n"
        f"## Direction: {meta.get('direction','')}\n"
        f"## Parent: {meta.get('parent_id','')}\n"
        f"## Mutation directive applied: {meta.get('mutation_directive','')}\n\n"
        f"## Code excerpt (HTML):\n```html\n{html_excerpt}\n```"
    )

    ok, _ = budget.can_spend(lib_claude.BEDROCK_COST_ESTIMATE_USD)
    if not ok:
        return None
    try:
        text, provider = lib_claude.call(system, user)
    except lib_claude.ProviderError:
        return None

    # extract JSON
    m = re.search(r"\{[\s\S]*\}", text)
    if not m: return None
    try:
        parsed = json.loads(m.group(0))
    except Exception:
        return None

    cost = 0.0 if provider == "subscription" else lib_claude.BEDROCK_COST_ESTIMATE_USD
    budget.record(cost, provider, note=f"swarm-debate {piece_id}")

    entry = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "piece": piece_id,
        "provider": provider,
        "cost_usd": cost,
        "verdicts": parsed,
    }
    ADVICE.parent.mkdir(parents=True, exist_ok=True)
    with ADVICE.open("a") as f:
        f.write(json.dumps(entry) + "\n")

    # also stash into the piece's meta.json for permanence
    meta.setdefault("swarm_debate", []).append(entry)
    meta_path.write_text(json.dumps(meta, indent=2))
    return entry


def recent_advice(n: int = 4) -> list[dict]:
    if not ADVICE.exists(): return []
    lines = ADVICE.read_text().splitlines()[-n:]
    out = []
    for ln in lines:
        try: out.append(json.loads(ln))
        except Exception: pass
    return out


def advice_block() -> str:
    """Format the last few swarm-debate verdicts for injection into the next prompt."""
    recent = recent_advice(4)
    if not recent: return ""
    lines = ["RECENT SWARM-PANEL DIRECTIVES (treat as soft guidance — pick what fits):"]
    for r in recent:
        v = r.get("verdicts", {})
        for persona, verdict in v.items():
            d = (verdict or {}).get("directive", "").strip()
            if d:
                lines.append(f"  - [{persona} on {r.get('piece')}] {d}")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: swarm_debate.py <piece_id>", file=sys.stderr)
        sys.exit(2)
    res = debate(sys.argv[1])
    if not res:
        print("no debate emitted (budget exhausted or provider failure or missing piece)", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(res, indent=2))
