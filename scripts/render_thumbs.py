#!/usr/bin/env python3
"""
Render thumbnails for every piece via headless Chromium (Playwright).
Reads lineage.json, screenshots pieces/<id>/index.html, writes thumbs/<id>.png.

Used by CI (.github/workflows/build-thumbs.yml) and locally:
    pip install playwright && playwright install chromium
    python3 scripts/render_thumbs.py
"""
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parent.parent
LINEAGE = REPO / "lineage.json"
THUMBS = REPO / "thumbs"
THUMBS.mkdir(exist_ok=True)

# size + warmup time per piece
W, H = 800, 500
WARMUP_MS_DEFAULT = 2500
WARMUP_MS_MODEL_LOADING = 9000   # pieces that fetch a 3D model from a CDN need more time


def _warmup_for(piece_id: str) -> int:
    meta_path = REPO / "pieces" / piece_id / "meta.json"
    if not meta_path.exists():
        return WARMUP_MS_DEFAULT
    try:
        meta = json.loads(meta_path.read_text())
    except Exception:
        return WARMUP_MS_DEFAULT
    # any piece that declares a model source needs the longer warmup
    if meta.get("model_source"):
        return WARMUP_MS_MODEL_LOADING
    stack = meta.get("stack", [])
    if any("Loader" in s for s in stack):
        return WARMUP_MS_MODEL_LOADING
    return WARMUP_MS_DEFAULT


def render(only=None):
    lineage = json.loads(LINEAGE.read_text())
    pieces = lineage["pieces"]
    if only:
        pieces = [p for p in pieces if p["id"] in only]
    if not pieces:
        print("no pieces to render", file=sys.stderr)
        return

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=[
            "--use-gl=swiftshader",
            "--enable-webgl",
            "--no-sandbox",
        ])
        ctx = browser.new_context(
            viewport={"width": W, "height": H},
            device_scale_factor=2,
        )
        for p in pieces:
            piece_html = REPO / "pieces" / p["id"] / "index.html"
            if not piece_html.exists():
                print(f"  skip {p['id']} — missing index.html", file=sys.stderr)
                continue
            url = f"file://{piece_html}"
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="load")
                warmup = _warmup_for(p["id"])
                page.wait_for_timeout(warmup)
                out = THUMBS / f"{p['id']}.png"
                page.screenshot(path=str(out), type="png")
                print(f"  ok  {p['id']} (warmup {warmup}ms) → {out.relative_to(REPO)}")
            except Exception as e:
                print(f"  fail {p['id']}: {e}", file=sys.stderr)
            finally:
                page.close()
        ctx.close()
        browser.close()


if __name__ == "__main__":
    only = set(sys.argv[1:]) if len(sys.argv) > 1 else None
    render(only=only)
