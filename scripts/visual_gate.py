#!/usr/bin/env python3
"""VLM visual gate — screenshot → Claude vision → pass / fail / iterate.

Runs AFTER validate_render.py (so the piece paints something).
Reads the thumbnail already saved to thumbs/<pid>.png by validate_render.

Returns:
  verdict      "pass" | "fail" | "iterate"
  reason       one-sentence explanation
  constraints  if "iterate": exact text to inject into remake prompt

Defaults to "pass" on any provider error so the gate is non-fatal.
"""
import base64, json, re, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TASTE = REPO / "taste.json"
BUG_MD = REPO / "bug.md"


def _context_block() -> str:
    lines = []
    if TASTE.exists():
        try:
            t = json.loads(TASTE.read_text())
            principles = (t.get("principles") or [])[:7]
            if principles:
                lines.append("TASTE PRINCIPLES:")
                for p in principles:
                    lines.append(f"  • {p}")
        except Exception:
            pass
    if BUG_MD.exists():
        lines.append("\nBUG.MD ANTI-PATTERNS (reject if visually present):")
        lines.append(BUG_MD.read_text()[:2500])
    return "\n".join(lines)


_SYSTEM = None  # built once, cached


def _get_system() -> str:
    global _SYSTEM
    if _SYSTEM is not None:
        return _SYSTEM
    ctx = _context_block()
    _SYSTEM = f"""\
You are a visual quality critic for an evolutionary generative-art gallery.
You will see a screenshot of a WebGL piece rendered headless. Assess it.

{ctx}

VERDICT DEFINITIONS
  pass    — gallery-quality: clear subject, strong contrast, intentional motion,
            form occupies >10% of frame, readable at a glance. Would say YES to at
            least 1 of the 3-question MoMA / Hollywood / startup-landing-page test.
  iterate — has a fixable problem. The structure/concept is salvageable but one
            specific thing is wrong: blurry sprites, too faint, subject too small,
            motion too fast/jittery, palette incoherent. Give exact constraints.
  fail    — irredeemable: pure noise blob with no form, subject invisible against
            background, rendering so broken the concept is unrecognizable.
            Reserve for truly hopeless cases — prefer "iterate" when in doubt.

Return STRICT JSON only, no prose outside the braces:
{{
  "verdict": "pass" | "fail" | "iterate",
  "reason": "one sentence max",
  "issues": ["list of specific observed problems — be concrete"],
  "constraints": "if iterate: verbatim constraint text to inject into the remake prompt so the model fixes the issue; else empty string"
}}
"""
    return _SYSTEM


def check(
    piece_id: str,
    thumb_path: Path,
    parent: dict,
    directive: str,
    attempt: int = 0,
) -> tuple[str, str, str]:
    """
    Returns (verdict, reason, constraints).
    Defaults to ("pass", reason, "") on any infrastructure failure.
    """
    if not thumb_path.exists():
        print(f"  visual gate: SKIPPED — no thumbnail at {thumb_path}")
        return "pass", "no thumbnail", ""

    try:
        sys.path.insert(0, str(REPO / "scripts"))
        import lib_claude
    except ImportError:
        return "pass", "lib_claude unavailable", ""

    user_text = (
        f"Piece: {piece_id}  (VLM check attempt {attempt + 1}/3)\n"
        f"Parent: {parent.get('id', '?')} — {parent.get('title', '?')}\n"
        f"Direction: {parent.get('direction', '?')}\n"
        f"Directive: {directive[:300]}\n\n"
        "Assess the screenshot."
    )

    try:
        b64 = base64.b64encode(thumb_path.read_bytes()).decode()
        raw = lib_claude.call_bedrock_vision(
            _get_system(), user_text, b64, "image/png", max_tokens=600
        )
    except Exception as e:
        print(f"  visual gate: SKIPPED (vision call failed: {e})")
        return "pass", str(e), ""

    m = re.search(r"\{[\s\S]*?\}", raw)
    if not m:
        print("  visual gate: SKIPPED (no JSON in response)")
        return "pass", "no json", ""
    try:
        parsed = json.loads(m.group(0))
    except Exception:
        return "pass", "json parse error", ""

    verdict = str(parsed.get("verdict", "pass")).lower()
    if verdict not in ("pass", "fail", "iterate"):
        verdict = "pass"
    reason = str(parsed.get("reason", ""))
    issues = parsed.get("issues") or []
    constraints = str(parsed.get("constraints", "")) if verdict == "iterate" else ""

    label = {"pass": "✓ PASS", "fail": "✗ FAIL", "iterate": "↻ ITERATE"}[verdict]
    print(f"  visual gate [{attempt + 1}/3]: {label} — {reason}")
    for iss in issues:
        print(f"    · {iss}")

    return verdict, reason, constraints


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: visual_gate.py <piece_id>"); sys.exit(2)
    pid = sys.argv[1]
    thumb = REPO / "thumbs" / f"{pid}.png"
    meta_path = REPO / "pieces" / pid / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    parent = {"id": meta.get("parent_id"), "title": "", "direction": meta.get("direction", "")}
    directive = meta.get("mutation_directive", "")
    v, r, c = check(pid, thumb, parent, directive)
    print(f"verdict={v!r}  reason={r!r}")
    if c:
        print(f"constraints={c!r}")
    sys.exit(0 if v == "pass" else 1)
