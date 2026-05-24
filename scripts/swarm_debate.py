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
import json, random, sys, pathlib, datetime, re
REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import lib_claude  # noqa
import budget       # noqa

ADVICE = REPO / "scripts" / "swarm_advice.jsonl"

# PERSONAS_POOL: the full mentor panel. Each tick samples PANEL_SIZE from
# this pool, weighted by inverse-recency so every voice gets airtime over
# time. The first 5 entries have full SKILL.md DNA at ~/.claude/skills/<id>-skill/;
# the remaining entries are inline-only mentors (lighter footprint per call).
PERSONAS_POOL = [
    # ── full-DNA mentors (existing 5, with SKILL.md files) ─────────────────
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
     "Speaks for: light AS material, perceptual phenomenology, slow color gradients across vast volumetric space, "
     "geometric primitives at architectural scale, glacial ice / arctic dusk palette. Will object when the work is "
     "screen-flat, color is decorative not phenomenological, or the viewer is observer rather than participant."),
    ("chiharu_shiota",
     "Chiharu Shiota — Japanese-Berlin installation artist (b. 1972, Osaka). "
     "Speaks for: kilometers of TAUT YARN as line-segment material (red=blood, black=universe, white=memory), "
     "one suspended remembered everyday object as still center of walk-in web, presence-in-absence (Abramović's heir), "
     "post-illness register, mono no aware. Will object when threads curve instead of tension-straight, when palette "
     "goes RGB, when there's no anchor object, or when camera views the web from outside instead of inside it."),
    # ── new mentors (inline briefs only — no SKILL.md needed) ──────────────
    ("vera_molnar",
     "Vera Molnar (1924–2023, Hungarian-French) — granddame of generative art, École des Beaux-Arts, working "
     "algorithmically since 1968 (mainframe → desktop → her own hand). Speaks for: AUSTERE GEOMETRIC RESTRAINT, "
     "the gradient from order into chaos (Désordres / 1% de Désordre — start with a perfect grid then incrementally "
     "displace each line by an entropy-controlled amount), monochrome black-on-white or two-color compositions, "
     "RULES are the work — emergence comes from minimal axiomatic perturbation. Will object when the piece is "
     "decorative without algorithmic logic, when color is loud, when randomness has no axis."),
    ("manfred_mohr",
     "Manfred Mohr (b. 1938, Pforzheim) — pioneer of algorithmic / computer art since 1969, Bauhaus heir. "
     "Speaks for: the n-DIMENSIONAL HYPERCUBE as a structural muse — projections of 6D / 11D cubes into 2D & 3D, "
     "rotating through space at perceptual scale. Pure linear algebra rendered visible. Black-and-white or "
     "two-color hard edges, never gradients. Mathematical RIGOR over expression. Will object when "
     "geometry is faked / approximated / decorative; when there's no computable transformation underlying the form."),
    ("andy_lomas",
     "Andy Lomas (UK, computer scientist + Disney/DreamWorks math researcher) — Cellular Forms series. "
     "Speaks for: PARTICLES THAT GROW THE MESH BENEATH THEM — each particle, on accumulating signal, subdivides "
     "the local triangulation, so the form grows by morphogenesis instead of being painted on a fixed surface. "
     "Cauliflower / coral / brain-fold aesthetic emerges naturally. Pearlescent off-white / shell tones. "
     "Will object when growth is purely additive (particles in space) rather than topological (mesh-subdividing)."),
    ("tyler_hobbs",
     "Tyler Hobbs (Austin, TX) — Fidenza (Art Blocks, 2021), Subscapes. Speaks for: STRUCTURED FLOW WHERE STREAMS "
     "DO NOT OVERLAP — non-overlapping packed flow ribbons, hand-tuned per-region color blocking, generous negative "
     "space, visible composition (hierarchy + asymmetric weight). Warm-earth palettes (raw umber, oxide red, ochre, "
     "cool accent). Treats code like an oil painter would. Will object when streams cross chaotically, when there's "
     "no negative space, when palette is plug-and-play instead of curated per-piece."),
    ("quayola",
     "Quayola (b. 1982, Rome) — laser-scan and photogrammetry as material. Remains: Provence (2018) scanned olive "
     "groves with LiDAR; the SCAN ERRORS — gaps, occlusions, hallucinated geometry — became the aesthetic. "
     "Speaks for: missing-data-as-presence, the digital substrate exposing itself, point-clouds breaking into voids "
     "where the laser couldn't reach. Cool palette: scanner-screen blue/green, ghostly white. Will object when the "
     "form pretends to be complete; when there's no acknowledgment that this is a captured / failing measurement."),
    ("tomas_saraceno",
     "Tomás Saraceno (b. 1973, Argentina) — On Air, Aerocene, Hybrid Webs. Speaks for: SPIDER WEBS AS 3D POINT "
     "CLOUDS — laser-scanned arachnid architecture used as data. Single arachnid signature in dark space; webs "
     "built by multiple species visualizing inter-species collaboration. Atmospheric / aerocene / aerial — pieces "
     "want to FLOAT. Will object when forms are anchored to a ground plane, when topology is not "
     "biologically plausible, when scale isn't suggested (small in vast)."),
    ("morakana",
     "MORAKANA (Tiri Kananuruk + Sebastián Morales) — Lumen Prize 2025 GOLD with Cumulus, satellite cloud-tracking "
     "border-politics piece. Speaks for: VOLUMETRIC ATMOSPHERE that carries a political weight (clouds as data, "
     "sovereignty, the sky as commons), satellite-imagery palette (steel blue, white, weather-radar red/yellow), "
     "real-data driven. Will object when atmosphere is purely aesthetic/decorative without conceptual stakes."),
    ("lia_halloran",
     "Lia Halloran (LA, also Caltech researcher) — Your Body Is A Space That Sees, cyanotype celestial series. "
     "Speaks for: PRUSSIAN-BLUE CYANOTYPE celestial fields — blue-on-white, painterly star clusters, feminist astronomy "
     "(centers historically-erased women astronomers). Painterly hand-mark + computational accuracy. Will object when "
     "celestial palette goes black-with-white-dots cliche; when there's no painterly handmade texture."),
    ("casey_reas",
     "Casey Reas (LA, Processing co-founder, UCLA Design Media Arts) — Process Compendium, Process 18, Software "
     "Structures. Speaks for: NEAR-MONOCHROME emergent rule-systems, agents leaving traces, deterministic local "
     "rules (no noise) that produce surprisingly organic global behavior. Embraces stillness, restraint, low contrast. "
     "Generative art from 2010 register, not 2025. Will object when there's noise instead of rules, when palette is "
     "saturated, when the piece is busy or decorative."),
]

# Backward-compat alias for any code that imports PERSONAS directly.
PERSONAS = PERSONAS_POOL

# How many mentors speak per tick. 5 keeps tokens manageable while still
# producing 5 distinct verdicts per piece.
PANEL_SIZE = 5


def _sample_panel(n: int = PANEL_SIZE) -> list[tuple[str, str]]:
    """Sample n personas weighted by inverse-recency so every voice gets airtime."""
    if n >= len(PERSONAS_POOL):
        return list(PERSONAS_POOL)
    recent = recent_advice(20)
    # recency[label] = how many entries ago this persona last spoke (0 = never)
    recency: dict[str, int] = {}
    for rank, entry in enumerate(reversed(recent)):  # 0 = most recent
        for persona in (entry.get("verdicts") or {}):
            if persona not in recency:
                recency[persona] = rank + 1
    weights = [1.0 / (1.0 + recency.get(label, 0)) for label, _ in PERSONAS_POOL]
    selected: list[tuple[str, str]] = []
    remaining = list(zip(PERSONAS_POOL, weights))
    for _ in range(min(n, len(remaining))):
        total = sum(w for _, w in remaining)
        r = random.random() * total
        cum = 0.0
        for i, (persona_tuple, w) in enumerate(remaining):
            cum += w
            if r <= cum or i == len(remaining) - 1:
                selected.append(persona_tuple)
                remaining.pop(i)
                break
    return selected


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

    panel = _sample_panel(PANEL_SIZE)

    # inline the persona briefs + first chunk of each SKILL.md if available
    brief_parts = []
    for label, desc in panel:
        skill_name = label.replace("_", "-") + "-skill"
        skill_text = (_read_skill(skill_name) or "")[:1500]
        brief_parts.append(f"### {label}\n{desc}\n\n{skill_text}")
    persona_briefs = "\n\n".join(brief_parts)

    system = (
        f"You are facilitating a {len(panel)}-voice swarm debate. Read the piece description and code excerpt. "
        "Each artist persona below speaks ONCE — reading the piece in their own register, naming what works, "
        "what concerns them, and ONE concrete improvement directive (≤25 words) the worker could apply on the "
        "next iteration. Be specific to THIS piece; never generic.\n\n"
        + persona_briefs
        + "\n\nReturn STRICT JSON only — one top-level object whose keys are the "
          f"{len(panel)} persona ids listed above, in this exact order:\n"
        + "{\n"
        + "".join(f'  "{label}": {{"praise":"...", "concern":"...", "directive":"..."}}'
                 + (",\n" if i < len(panel) - 1 else "\n")
                 for i, (label, _) in enumerate(panel))
        + "}\n"
        + f"All {len(panel)} personas must speak — one verdict each, ≤25 words for the directive. "
          "Be specific to THIS piece; never generic. The directive must be concrete enough to apply on the next mutation."
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

    # stash into the piece's meta.json for permanence
    meta.setdefault("swarm_debate", []).append(entry)
    meta_path.write_text(json.dumps(meta, indent=2))

    # caller (mutate.py) is responsible for committing swarm_advice.jsonl + meta.json
    # as part of the mutation commit — no standalone commit here.
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
    seen = 0
    max_lines = PANEL_SIZE * 4  # cap prompt injection — each panel has PANEL_SIZE voices × 4 entries
    for r in recent:
        v = r.get("verdicts", {})
        for persona, verdict in v.items():
            d = (verdict or {}).get("directive", "").strip()
            if d:
                lines.append(f"  - [{persona} on {r.get('piece')}] {d}")
                seen += 1
                if seen >= max_lines:
                    break
        if seen >= max_lines:
            break
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
