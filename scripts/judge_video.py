#!/usr/bin/env python3
"""Video-based aesthetic judge (issue #13).

Watches the captured dynamic loop of a piece (capture_loop.py webm) and
scores it against taste.json. Two interchangeable backends:

    gemini   — Gemini 2.5 Flash, native video input (last 8s, h264 mp4)
    claude   — Claude Sonnet 4.5 via Bedrock, 6 frames sampled from the
               same 8s window (Claude has no video input)

Both return strict JSON per piece:
    {"score": 1-10, "verdict": "<≤2 sentences why>", "resembles": "<named
     contemporary artwork / architecture / biomorphic system>"}

The judge sees ONLY the video + taste profile — never the user's marks
(no label leakage).

Usage:
    scripts/judge_video.py --backend gemini --clips-dir /tmp/pa_eval/clips xs4 zs4
    scripts/judge_video.py --backend claude --ids-file ids.txt --out results.jsonl
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FFMPEG = os.environ.get("FFMPEG", "/opt/homebrew/bin/ffmpeg")

BEDROCK_REGION = os.environ.get("AWS_REGION", "us-east-1")
CLAUDE_MODEL = os.environ.get(
    "PARTICLE_ART_BEDROCK_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
GEMINI_MODEL = os.environ.get("PARTICLE_ART_GEMINI_MODEL", "gemini-2.5-flash")

WINDOW_S = 8          # steady-state window taken from the END of the capture
N_FRAMES = 6          # frames sampled for the claude backend


# ---------------------------------------------------------------- prompt

def _taste_summary() -> str:
    t = json.loads((REPO / "taste.json").read_text())
    likes = t.get("likes", {}).get("directions", [])
    dl = t.get("dislikes", {})
    parts = ["GALLERY LIKES:"] + [f"- {x}" for x in likes]
    if dl.get("directions"):
        parts += ["", "GALLERY DISLIKES:"] + [f"- {x}" for x in dl["directions"]]
    if dl.get("techniques"):
        parts += ["", "BANNED (auto-fail if dominant):"] + [f"- {x}" for x in dl["techniques"]]
    return "\n".join(parts)


def build_prompt() -> str:
    return f"""You are the aesthetic judge for a curated gallery of generative particle art \
(three.js + GLSL). You are watching an ~{WINDOW_S}-second capture of one piece's live loop \
(the first second or two may still be initializing — judge the settled state).

This is an ABSTRACT, generative gallery. Structured abstraction IS the medium, not a defect: \
strange attractors (Lorenz/Aizawa butterflies & vortices), differential growth, L-systems, \
flow/vector fields, point-cloud objects, calligraphic ribbons, and network/vascular forms are \
all PRIME material here. "Not figurative" is NOT a reason to score low — a Lorenz attractor or \
a coral-like growth is a strong form even though it depicts no object.

{_taste_summary()}

What actually separates good from bad here:
- GOOD: a coherent form or field that reads as INTENTIONAL within ~2s; restrained / duotone \
palette; motion that is contemplative and lets you read the form (slow orbit, breathing, growth).
- BAD: genuinely formless ambient haze with no structure; near-empty / barely-visible renders; \
illegible mush; blown-out neon; or broken/half-loaded frames.

CALIBRATION — use the FULL 1-10 range; do NOT pile everything at 2-3:
  9-10 = exceptional, signature wall piece
  7-8  = clearly good, earns its place
  5-6  = competent but unremarkable / borderline
  3-4  = weak: thin structure, muddy, or forgettable
  1-2  = empty, broken, or pure formless noise
Most pieces should land 4-7. Reserve 1-2 for genuinely empty/broken renders only.

Do NOT try to detect code-level issues (autoRotate, shaking) from the video — a separate static \
checker handles those. Judge ONLY the visual & motion result. A slow continuous camera orbit that \
reads as contemplative is fine; only penalize motion that is jittery, frantic, or dizzying.

Return STRICT JSON only, no prose, no fences:
{{
  "score": <int 1-10 per the calibration above>,
  "verdict": "<concise, ≤2 sentences: the decisive reason for the score, naming form AND motion>",
  "resembles": "<ONE specific, well-known reference this piece most evokes — a contemporary \
artwork/artist (e.g. 'Refik Anadol — Machine Hallucinations'), a building/architect \
(e.g. 'Calatrava ribbed vault'), or a biomorphic/natural system (e.g. 'physarum plasmodium \
network', 'starling murmuration'). Name the closest one even for weak pieces.>"
}}"""


# ---------------------------------------------------------------- media prep

def trim_window(clip: Path, out_mp4: Path) -> None:
    """Last WINDOW_S seconds, h264 mp4 (gemini-friendly, small)."""
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-sseof", f"-{WINDOW_S}", "-i", str(clip),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-an", str(out_mp4)],
        check=True)


def extract_frames(clip: Path, out_dir: Path) -> list[Path]:
    """N_FRAMES JPEGs evenly across the last WINDOW_S seconds."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = out_dir / "f%02d.jpg"
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-sseof", f"-{WINDOW_S}", "-i", str(clip),
         "-vf", f"fps={N_FRAMES}/{WINDOW_S}", "-frames:v", str(N_FRAMES),
         "-q:v", "4", str(pattern)],
        check=True)
    return sorted(out_dir.glob("f*.jpg"))


def frame_montage(clip: Path, out_jpg: Path) -> None:
    """N_FRAMES sampled across the window, tiled into one labelled grid image.
    A single image keeps the subscription CLI call light while still conveying
    motion (time reads left→right, top→bottom)."""
    cols = 3
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-sseof", f"-{WINDOW_S}", "-i", str(clip),
         "-vf", f"fps={N_FRAMES}/{WINDOW_S},scale=400:-1,"
                f"tile={cols}x{(N_FRAMES + cols - 1)// cols}:padding=4:color=black",
         "-frames:v", "1", "-q:v", "4", str(out_jpg)],
        check=True)


# ---------------------------------------------------------------- backends

def judge_gemini(clip: Path, prompt: str) -> str:
    from google import genai
    from google.genai import types
    client = genai.Client()  # GEMINI_API_KEY / GOOGLE_API_KEY env
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        mp4 = Path(f.name)
    try:
        trim_window(clip, mp4)
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                types.Part.from_bytes(data=mp4.read_bytes(), mime_type="video/mp4"),
                prompt,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json", temperature=0.2),
        )
        return resp.text
    finally:
        mp4.unlink(missing_ok=True)


def judge_claude(clip: Path, prompt: str) -> str:
    import boto3
    client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
    with tempfile.TemporaryDirectory() as td:
        frames = extract_frames(clip, Path(td))
        content = []
        for i, fp in enumerate(frames):
            content.append({"type": "text",
                            "text": f"[frame {i+1}/{len(frames)}, t≈{i*WINDOW_S/N_FRAMES:.1f}s]"})
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg",
                "data": base64.b64encode(fp.read_bytes()).decode()}})
        content.append({"type": "text", "text": prompt})
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 400,
        "temperature": 0.2,
        "messages": [{"role": "user", "content": content}],
    }
    resp = client.invoke_model(modelId=CLAUDE_MODEL, body=json.dumps(body))
    out = json.loads(resp["body"].read())
    return "".join(b.get("text", "") for b in out.get("content", []))


def judge_subscription(clip: Path, prompt: str) -> str:
    """Claude via the `claude` CLI subscription ($0). Vision through
    --input-format stream-json: one montage image (frames over time) + prompt."""
    with tempfile.TemporaryDirectory() as td:
        montage = Path(td) / "m.jpg"
        frame_montage(clip, montage)
        img = base64.b64encode(montage.read_bytes()).decode()
        msg = {"type": "user", "message": {"role": "user", "content": [
            {"type": "text",
             "text": f"This is a {N_FRAMES}-frame montage of one piece's loop, "
                     f"time order left→right, top→bottom (motion reads across the tiles)."},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img}},
            {"type": "text", "text": prompt},
        ]}}
    p = subprocess.run(
        ["claude", "-p", "--input-format", "stream-json",
         "--output-format", "stream-json", "--verbose"],
        input=json.dumps(msg) + "\n", capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        raise RuntimeError(f"claude cli rc={p.returncode}: {p.stderr[:200]}")
    for line in p.stdout.splitlines():
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("type") == "result" and isinstance(o.get("result"), str):
            return o["result"]
    raise RuntimeError("no result line from claude cli")


def parse_result(text: str) -> dict | None:
    import re
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        return {"score": max(1, min(10, int(d["score"]))),
                "verdict": str(d.get("verdict", ""))[:400],
                "resembles": str(d.get("resembles", ""))[:200]}
    except Exception:
        return None


# ---------------------------------------------------------------- runner

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--ids-file", type=Path)
    ap.add_argument("--backend", choices=["gemini", "claude", "subscription"], required=True)
    ap.add_argument("--clips-dir", type=Path, default=REPO / "clips_judge")
    ap.add_argument("--out", type=Path, help="results jsonl (appends; skips already-judged ids)")
    ap.add_argument("--workers", type=int, default=1,
                    help="parallel judge calls (subscription path only)")
    args = ap.parse_args()

    ids = list(args.ids)
    if args.ids_file:
        ids += [l.strip() for l in args.ids_file.read_text().splitlines() if l.strip()]
    if not ids:
        ap.error("no piece ids")

    done = set()
    if args.out and args.out.exists():
        for line in args.out.read_text().splitlines():
            try:
                done.add(json.loads(line)["id"])
            except Exception:
                pass

    prompt = build_prompt()
    fn = {"gemini": judge_gemini, "claude": judge_claude,
          "subscription": judge_subscription}[args.backend]
    model_tag = {"gemini": GEMINI_MODEL, "claude": CLAUDE_MODEL,
                 "subscription": "claude-cli-subscription"}[args.backend]
    out_f = args.out.open("a") if args.out else None

    todo = []
    for pid in ids:
        if pid in done:
            continue
        clip = args.clips_dir / f"{pid}.webm"
        if not clip.exists():
            print(f"  ✗ {pid}: no clip", flush=True)
            continue
        todo.append((pid, clip))

    def judge_one(item):
        pid, clip = item
        rec = {"id": pid, "backend": args.backend, "model": model_tag}
        for attempt in range(3):
            try:
                parsed = parse_result(fn(clip, prompt))
                if parsed:
                    rec.update(parsed)
                    break
            except Exception as e:
                rec["error"] = str(e)[:200]
                time.sleep(5 * (attempt + 1))
        return rec

    def emit(rec):
        marker = "✓" if "score" in rec else "✗"
        print(f"  {marker} {rec['id']} [{args.backend}] "
              f"{rec.get('score','—')} {rec.get('verdict', rec.get('error',''))[:90]}", flush=True)
        if out_f:
            out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out_f.flush()

    if args.workers > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            for rec in ex.map(judge_one, todo):
                emit(rec)
    else:
        for item in todo:
            emit(judge_one(item))
    if out_f:
        out_f.close()


if __name__ == "__main__":
    main()
