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
WARMUP_MS = 2500


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
                page.wait_for_timeout(WARMUP_MS)
                out = THUMBS / f"{p['id']}.png"
                page.screenshot(path=str(out), type="png")
                print(f"  ok  {p['id']} → {out.relative_to(REPO)}")
            except Exception as e:
                print(f"  fail {p['id']}: {e}", file=sys.stderr)
            finally:
                page.close()
        ctx.close()
        browser.close()


if __name__ == "__main__":
    only = set(sys.argv[1:]) if len(sys.argv) > 1 else None
    render(only=only)
