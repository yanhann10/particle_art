"""shadertoy_learner.py — fetch Shadertoy weekly shaders, extract GLSL scaffolds
into scripts/shader_lib/.  --demo needs no key; live run needs SHADERTOY_KEY."""
import argparse
import os
import re
import sys
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "scripts" / "shader_lib"
README_PATH = LIB_DIR / "README.md"

API_BASE = "https://www.shadertoy.com/api/v1"
DEMO_IDS = ["XlSSzK", "WdBfDd", "NtlSDs", "3lsSzf"]

SYSTEM_PROMPT = (
    "You are a GLSL shader analyst for a generative art system. "
    "Analyze this Shadertoy GLSL fragment shader source. "
    "Extract the single most reusable, technique-defining 30–80 line section. "
    "Then write a self-contained scaffold file: a header doc comment (what it produces, "
    "what to vary), followed by the extracted code. "
    "Return ONLY the scaffold file content, no extra prose."
)


def fetch_weekly_ids(key: str) -> list[str]:
    r = requests.get(f"{API_BASE}/shaders/query/week?sort=popular&from=0&num=6&key={key}", timeout=15)
    r.raise_for_status()
    return r.json().get("Results", [])


def fetch_glsl(shader_id: str, key: str) -> str:
    r = requests.get(f"{API_BASE}/shaders/{shader_id}?key={key}", timeout=15)
    r.raise_for_status()
    try:
        return r.json()["Shader"]["renderpass"][0]["code"]
    except (KeyError, IndexError) as exc:
        raise ValueError(f"unexpected response shape for {shader_id}") from exc


def call_claude(glsl_src: str) -> str:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from lib_claude import call  # type: ignore
    text, provider = call(SYSTEM_PROMPT, glsl_src[:4000])
    print(f"  [claude/{provider}] got {len(text)} chars")
    return text


def extract_technique_name(scaffold: str) -> str:
    for line in scaffold.splitlines():
        line = line.strip().lstrip("/*").strip()
        if line and not line.startswith("*"):
            return re.sub(r"[.,:;]+$", "", line)[:50]
    return "unknown technique"


def append_readme(shader_id: str, technique: str) -> None:
    row = f"| `sotw_{shader_id}.glsl` | {technique} | shadertoy.com/view/{shader_id} |\n"
    content = README_PATH.read_text()
    if f"sotw_{shader_id}.glsl" not in content:
        README_PATH.write_text(content + row)
        print(f"  README updated: {row.strip()}")


def process_shader(shader_id: str, key: str) -> None:
    out_path = LIB_DIR / f"sotw_{shader_id}.glsl"
    if out_path.exists():
        print(f"[{shader_id}] already in lib, skipping"); return
    try:
        glsl = fetch_glsl(shader_id, key)
    except Exception as exc:
        print(f"[{shader_id}] fetch failed: {exc}"); return
    print(f"[{shader_id}] calling Claude ({len(glsl)} chars) …")
    try:
        scaffold = call_claude(glsl)
    except Exception as exc:
        print(f"[{shader_id}] Claude failed: {exc}"); return
    if not scaffold or len(scaffold) <= 20:
        print(f"[{shader_id}] trivial response, skipping"); return
    out_path.write_text(scaffold)
    print(f"[{shader_id}] wrote {out_path.relative_to(REPO_ROOT)}")
    append_readme(shader_id, extract_technique_name(scaffold))


def main() -> None:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group()
    g.add_argument("--demo", action="store_true", help="use hardcoded IDs (no key needed)")
    g.add_argument("--id", metavar="ID", help="process one shader ID")
    args = p.parse_args()

    key = os.environ.get("SHADERTOY_KEY", "")

    if args.id:
        if not key:
            sys.exit("ERROR: SHADERTOY_KEY not set. Use --demo or set SHADERTOY_KEY.")
        process_shader(args.id, key)
        return

    if args.demo or not key:
        if not args.demo and not key:
            print("No SHADERTOY_KEY — using demo IDs (pass --demo to suppress this).")
        ids = DEMO_IDS
        if not key:
            key = os.environ.get("SHADERTOY_DEMO_KEY", "bf06UrOr")
    else:
        print("Fetching weekly popular shader IDs …")
        try:
            ids = fetch_weekly_ids(key)
        except Exception as exc:
            sys.exit(f"Failed to fetch weekly IDs: {exc}")
        print(f"Got {len(ids)} IDs: {ids}")

    for shader_id in ids:
        process_shader(shader_id, key)
    print("Done.")


if __name__ == "__main__":
    main()
