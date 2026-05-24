#!/usr/bin/env python3
"""Pinterest Art Connoisseur Agent.

Reads pinterest_pins.json, downloads each pin image, and runs VLM analysis
from the perspective of a sophisticated art critic / connoisseur.

The critique drives the maker agent via priority_directive (highest-score pins
become mutation directives). Agent-to-agent messaging is wired separately.

Outputs structured critique to pinterest_critique.jsonl.

Usage:
    python scripts/pinterest_critic_agent.py [--input PATH] [--limit N] [--dry-run]
    python scripts/pinterest_critic_agent.py --summary          # print top picks from existing log
"""
import argparse
import base64
import json
import re
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()

REPO           = Path(__file__).resolve().parent.parent
DEFAULT_INPUT  = REPO / "scripts" / "pinterest_pins.json"
DEFAULT_OUTPUT = REPO / "scripts" / "pinterest_critique.jsonl"
TASTE          = REPO / "taste.json"

sys.path.insert(0, str(REPO / "scripts"))
import lib_claude


# ── Prompts ───────────────────────────────────────────────────────────────────

CRITIC_SYSTEM = (
    "You are a sophisticated art critic and visual connoisseur with deep expertise "
    "in generative art, computational aesthetics, and contemporary fine art. "
    "You have spent years in gallery spaces, absorbed art theory, and understand both "
    "traditional and computational/new-media art intimately. "
    "Your critiques are specific, technically grounded, emotionally intelligent, and generative — "
    "you always ask: what formal strategies here could be translated into particle/shader art?"
)

CRITIC_PROMPT_TMPL = """\
The artist pinned this image on their Pinterest inspiration board.
Pin description hint: {desc}

**Artist taste profile:**
{taste}

Analyze this image as a connoisseur advising a three.js + GLSL particle art system.
Respond with a JSON object ONLY — no prose outside the braces:

{{
  "formal_reading": "<2-3 sentences: composition, palette, spatial organization, scale, rhythm — specific and technical>",
  "material_thinking": "<1-2 sentences: what physical or digital technique creates this effect? name the method>",
  "conceptual_register": "<1-2 sentences: what idea, tension, or question does this image pose? what is it ABOUT?>",
  "emotional_affect": "<1 sentence: the emotional temperature — be precise, not generic ('quietly elegiac', 'kinetic and disorienting', etc.)>",
  "particle_art_vectors": "<2-3 concrete strategies this suggests for a particle system — name specific techniques e.g. 'GPGPU Lorenz attractor', 'differential growth with age-driven vertical drift', 'CatmullRomCurve3 tube along growth skeleton'>",
  "named_references": "<artists, art movements, or named techniques this echoes — specific, not generic>",
  "critique_summary": "<2-3 sentence synthesis: is this a strong inspiration source and why?>",
  "inspiration_score": <integer 1-10, where 10 = immediately actionable formal idea for the particle system>,
  "priority_directive": "<the single most actionable 10-20 word mutation directive this image suggests>"
}}"""


# ── Taste loader ──────────────────────────────────────────────────────────────

def _taste_summary() -> str:
    if not TASTE.exists():
        return "No taste profile."
    try:
        t = json.loads(TASTE.read_text())
    except Exception:
        return ""
    lines = []
    dirs = (t.get("likes") or {}).get("directions", [])
    if dirs:
        lines.append("LIKES: " + "; ".join(dirs[:8]))
    dis = (t.get("dislikes") or {}).get("directions", [])
    if dis:
        lines.append("DISLIKES: " + "; ".join(dis[:5]))
    prns = t.get("principles", [])
    if prns:
        lines.append("PRINCIPLES: " + "; ".join(prns[:3]))
    return "\n".join(lines)


# ── Image fetch ───────────────────────────────────────────────────────────────

def _download_b64(url: str) -> tuple[str, str]:
    req = urllib.request.Request(
        url, headers={"User-Agent": "particle-art-critic/1.0"}
    )
    with urllib.request.urlopen(req, context=_SSL_CTX, timeout=20) as r:
        data = r.read()
        ct   = r.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
    return base64.b64encode(data).decode(), ct


# ── JSON extraction ───────────────────────────────────────────────────────────

def _parse_json(raw: str) -> dict | None:
    raw = raw.strip()
    # strip markdown fences
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
    # first, try full parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # greedy JSON object hunt
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None


# ── Core critique ─────────────────────────────────────────────────────────────

def critique_pin(pin: dict, taste: str, dry_run: bool) -> dict | None:
    pin_id  = pin.get("id", "?")
    img_url = pin.get("image_url", "")
    desc    = (pin.get("description") or "")[:200]

    print(f"  → pin {pin_id}  {desc[:70]}")

    if dry_run:
        return {
            "formal_reading":       "DRY RUN — layered depth, restrained palette.",
            "material_thinking":    "DRY RUN — photographic texture with digital post.",
            "conceptual_register":  "DRY RUN — tension between order and emergence.",
            "emotional_affect":     "DRY RUN — quietly melancholic.",
            "particle_art_vectors": "DRY RUN — differential growth with depth stratification.",
            "named_references":     "DRY RUN — Agnes Martin, Robert Irwin.",
            "critique_summary":     "DRY RUN — strong formal source for restrained palette work.",
            "inspiration_score":    7,
            "priority_directive":   "DRY RUN directive",
        }

    if not img_url:
        print(f"    skip: no image URL")
        return None

    try:
        b64, media_type = _download_b64(img_url)
    except Exception as e:
        print(f"    image download failed: {e}")
        return None

    prompt = CRITIC_PROMPT_TMPL.format(desc=desc, taste=taste)

    try:
        raw = lib_claude.call_bedrock_vision(
            CRITIC_SYSTEM, prompt, b64, media_type,
            # use the project's configured Claude model (not Nova Pro default)
            model_id=lib_claude.BEDROCK_MODEL_ID,
            max_tokens=1400,
        )
    except lib_claude.ProviderError as e:
        print(f"    VLM failed: {e}")
        return None

    result = _parse_json(raw)
    if not result:
        print(f"    JSON parse failed: {raw[:120]}")
        return None

    # Clamp score
    try:
        result["inspiration_score"] = max(1, min(10, int(result["inspiration_score"])))
    except Exception:
        pass

    return result


# ── Summary printer ───────────────────────────────────────────────────────────

def print_summary(output_path: Path) -> None:
    if not output_path.exists():
        print("No critique log found.")
        return
    entries = [json.loads(l) for l in output_path.read_text().strip().splitlines() if l.strip()]
    if not entries:
        print("Critique log is empty.")
        return
    high = sorted(
        [e for e in entries if e.get("critique", {}).get("inspiration_score", 0) >= 7],
        key=lambda x: -x["critique"].get("inspiration_score", 0),
    )
    print(f"\n{'='*60}")
    print(f"Pinterest critique log: {len(entries)} total, {len(high)} high-score (≥7)")
    print()
    for e in high[:5]:
        c = e.get("critique", {})
        score = c.get("inspiration_score", "?")
        directive = c.get("priority_directive", "")
        affect    = c.get("emotional_affect", "")
        refs      = c.get("named_references", "")
        print(f"  [{score}/10] {directive}")
        print(f"          affect: {affect}")
        print(f"          refs:   {refs}")
        print(f"          {e.get('pin_url', '')}")
        print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Pinterest art critic agent")
    ap.add_argument("--input",   default=str(DEFAULT_INPUT))
    ap.add_argument("--output",  default=str(DEFAULT_OUTPUT))
    ap.add_argument("--limit",   type=int, default=10)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--summary", action="store_true", help="Print top critiques from existing log")
    ap.add_argument("--image",   default=None,
                    help="Critique a single image file or URL directly (skips pins input)")
    args = ap.parse_args()

    out_path = Path(args.output)

    if args.summary:
        print_summary(out_path)
        return

    # ── Single-image mode ──────────────────────────────────────────────────────
    if args.image:
        taste  = _taste_summary()
        source = args.image
        # Load from local file or URL
        if Path(source).exists():
            img_bytes = Path(source).read_bytes()
            ext = Path(source).suffix.lower()
            media_type = {"png": "image/png", "jpg": "image/jpeg",
                          "jpeg": "image/jpeg", "webp": "image/webp",
                          "gif": "image/gif"}.get(ext.lstrip("."), "image/jpeg")
            b64 = base64.b64encode(img_bytes).decode()
        else:
            print(f"Fetching {source}")
            b64, media_type = _download_b64(source)

        pin = {"id": "single", "url": source, "image_url": source, "description": ""}
        prompt = CRITIC_PROMPT_TMPL.format(desc="(screenshot or direct image)", taste=taste)
        print("Running art critic…")
        try:
            raw = lib_claude.call_bedrock_vision(
                CRITIC_SYSTEM, prompt, b64, media_type,
                model_id=lib_claude.BEDROCK_MODEL_ID,
                max_tokens=1400,
            )
        except lib_claude.ProviderError as e:
            sys.exit(f"VLM failed: {e}")

        result = _parse_json(raw)
        if not result:
            print("Raw response:", raw)
            sys.exit("JSON parse failed")

        print("\n" + "="*60)
        print(f"Aesthetic Critique")
        print("="*60)
        for key in ["formal_reading", "material_thinking", "conceptual_register",
                    "emotional_affect", "named_references", "particle_art_vectors",
                    "critique_summary"]:
            val = result.get(key, "")
            if val:
                label = key.replace("_", " ").title()
                print(f"\n{label}:\n  {val}")
        score = result.get("inspiration_score", "?")
        directive = result.get("priority_directive", "")
        print(f"\nInspiration Score: {score}/10")
        print(f"Priority Directive: {directive}")
        return

    inp_path = Path(args.input)
    if not inp_path.exists():
        sys.exit(f"Input not found: {inp_path}\nRun pinterest_scraper.py first.")

    pins  = json.loads(inp_path.read_text())
    taste = _taste_summary()
    print(f"Pinterest Critic Agent: {len(pins)} pins, analysing ≤{args.limit}")

    analysed = skipped = 0
    for pin in pins[: args.limit]:
        result = critique_pin(pin, taste, args.dry_run)
        if result is None:
            skipped += 1
            continue

        entry = {
            "pin_id":      pin.get("id"),
            "pin_url":     pin.get("url"),
            "image_url":   pin.get("image_url"),
            "description": pin.get("description", ""),
            "critique":    result,
            "analysed_at": datetime.now(timezone.utc).isoformat(),
        }

        if args.dry_run:
            print(json.dumps(entry, indent=2)[:600])
        else:
            with open(out_path, "a") as f:
                f.write(json.dumps(entry) + "\n")

        score = result.get("inspiration_score", "?")
        directive = result.get("priority_directive", "")[:70]
        print(f"    ✓ [{score}/10] {directive}")
        analysed += 1
        time.sleep(0.5)

    print(f"\nDone: {analysed} critiqued, {skipped} skipped")
    if not args.dry_run and analysed:
        print_summary(out_path)


if __name__ == "__main__":
    main()
