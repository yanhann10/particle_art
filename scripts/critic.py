"""LLM-as-judge for improv ticks.

Workflow per the user's 2026-05-06 directive:
    "assess whether it is a good execution of the linked idea, then
     aesthetically judge before and after refinement, select one ver
     to deploy"

So a critic that scores each candidate on TWO axes:
    1. execution_score      — does the HTML execute the linked idea well?
                              (e.g. for mode=artist with word=patient/Agnes
                              Martin, does the piece actually feel patient
                              and Martin-esque, or did it just paste the
                              parent code unchanged?)
    2. aesthetic_score      — separate from idea-fidelity: is this piece
                              visually compelling on its own merits given
                              the user's taste profile?

Both 1-10. Comes back with concrete `feedback` strings used to refine.

Loop:
    v1   = generate
    s1   = critic(v1, idea)
    v2   = regenerate(prompt + s1.feedback)
    s2   = critic(v2, idea)
    pick the higher combined score; loser saved to scripts/rejects/<id>/.
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import lib_claude


def _read_taste_summary() -> str:
    """One-paragraph taste reminder for the critic."""
    p = REPO / "taste.json"
    if not p.exists():
        return ""
    try:
        t = json.loads(p.read_text())
    except Exception:
        return ""
    likes = t.get("likes") or {}
    dislikes = t.get("dislikes") or {}
    parts = []
    # `likes` and `dislikes` are dicts keyed by category (directions / techniques / specific_pieces);
    # values are lists of strings.
    likes_dirs = likes.get("directions") if isinstance(likes, dict) else (likes if isinstance(likes, list) else [])
    if likes_dirs:
        parts.append("LIKES: " + "; ".join(likes_dirs[:6]))
    if dislikes.get("directions"):
        parts.append("DISLIKES: " + "; ".join(dislikes["directions"][:5]))
    return "\n".join(parts)


def judge(html: str, mode: str, word: str, extras: dict,
          parent_id: str, parent_title: str) -> dict:
    """Score an execution on idea-fidelity + aesthetic merit.

    Returns: {
        "execution_score": int (1-10),
        "aesthetic_score": int (1-10),
        "combined": float,
        "feedback": str (what to fix in the next iteration),
        "raw": str (full critic text)
    }
    """
    taste = _read_taste_summary()

    if mode == "artist":
        idea_brief = (
            f"mode=artist · personality word: '{word}' · channel artist: "
            f"{extras.get('artist','?')} · practice: {extras.get('practice','')}"
        )
    elif mode == "surprise":
        idea_brief = (
            f"mode=surprise · word: '{word}' · linked idea: a delightful, "
            f"tasteful tonal/scale/color swerve injected into a NEW BRANCH "
            f"of the gallery (parent: {parent_id} = {parent_title})"
        )
    else:  # chain
        idea_brief = (
            f"mode=chain · word: '{word}' · linked idea: continuous morph of "
            f"parent {parent_id} ({parent_title}) toward the word's vibe — "
            f"the piece must read as the parent after the word has passed "
            f"through it (NOT a fresh start)"
        )

    # Truncate HTML for the critic — first 8k chars carry the structural
    # gist (imports, scene setup, geometry, shader) without inflating cost.
    html_excerpt = html[:8000]
    if len(html) > 8000:
        html_excerpt += f"\n\n[... {len(html) - 8000} more chars omitted ...]"

    system = (
        "You are an aesthetic critic for a particle-art evolutionary gallery. "
        "You read ONE HTML particle-art piece and rate it on two independent "
        "axes given a brief that describes the linked idea the piece was "
        "supposed to execute. Be honest and specific — the user has explicit "
        "taste. Penalize: noise blobs without form, tiny subjects in vast "
        "empty canvases, fixed cameras when the form grows past frame, "
        "code that just renames a variable but doesn't actually morph the "
        "form, copy-pasted parent shader without the idea showing up. "
        "Reward: form readable in 2s, restrained palette, motion that earns "
        "its compute, signs that the linked idea actually drove the design."
    )
    user = f"""# Critique brief
{taste}

# Linked idea (what the piece was asked to execute)
{idea_brief}

# Candidate piece (HTML)
```html
{html_excerpt}
```

# Output (JSON only, no prose, no fences)
{{
  "execution_score": <int 1-10>,
  "aesthetic_score": <int 1-10>,
  "feedback": "<2-4 sentences: what specifically should change in a refined version. concrete and shader-aware.>"
}}
"""
    try:
        text, _provider = lib_claude.call(system, user)
    except lib_claude.ProviderError as e:
        # Critic failure → score at neutral 5; let v1 deploy
        return {"execution_score": 5, "aesthetic_score": 5, "combined": 5.0,
                "feedback": f"critic-error: {e}", "raw": ""}

    parsed = _parse_critic_json(text)
    if not parsed:
        return {"execution_score": 5, "aesthetic_score": 5, "combined": 5.0,
                "feedback": "critic-parse-failed", "raw": text}

    es = max(1, min(10, int(parsed.get("execution_score", 5))))
    as_ = max(1, min(10, int(parsed.get("aesthetic_score", 5))))
    fb = str(parsed.get("feedback", ""))[:1200]
    return {
        "execution_score": es,
        "aesthetic_score": as_,
        "combined": (es + as_) / 2.0,
        "feedback": fb,
        "raw": text,
    }


def _parse_critic_json(text: str) -> dict | None:
    """Extract first JSON object from critic response, tolerating prose/fences."""
    # ```json … ``` or ``` … ``` fence
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    cand = m.group(1) if m else None
    if not cand:
        m = re.search(r"\{[^{}]*\"execution_score\"[^{}]*\}", text, re.S)
        if m:
            cand = m.group(0)
    if not cand:
        # last-ditch: first {…} anywhere
        m = re.search(r"\{.*?\}", text, re.S)
        cand = m.group(0) if m else None
    if not cand:
        return None
    try:
        return json.loads(cand)
    except Exception:
        return None
