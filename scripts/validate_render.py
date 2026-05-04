#!/usr/bin/env python3
"""Pre-deploy renderer + validator.

Runs the same Playwright render pipeline as the CI thumbnail builder
(scripts/render_thumbs.py) but adds a *content check*: each rendered
thumbnail must contain a non-trivial amount of non-background pixels,
otherwise the piece is flagged as broken.

Used before pushing changes to Vercel — invoke with the piece IDs you
just edited:

    scripts/validate_render.py xs4 5gm hn4

Exit code:
    0  all pieces passed
    1  one or more pieces produced empty / near-empty thumbnails
    2  setup error (Playwright missing, etc.)
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PIECES = REPO / "pieces"
LINEAGE = REPO / "lineage.json"

# What counts as "broken"? If <0.5% of pixels are >5 channel-units brighter
# than the median (background) color, the piece is rendering essentially
# nothing — typically a shader compile failure or a misframed camera.
MIN_NONBACKGROUND_FRACTION = 0.005

W, H = 800, 500


def _warmup_for(piece_id: str) -> int:
    meta_path = PIECES / piece_id / "meta.json"
    if not meta_path.exists():
        return 2500
    try:
        m = json.loads(meta_path.read_text())
    except Exception:
        return 2500
    if isinstance(m.get("warmup_ms"), (int, float)):
        return int(m["warmup_ms"])
    if m.get("model_source") or any("Loader" in s for s in m.get("stack", [])):
        return 9000
    return 2500


def validate(piece_ids: list[str]) -> int:
    try:
        from playwright.sync_api import sync_playwright
        from PIL import Image
        import numpy as np
    except ImportError as e:
        print(f"missing dependency: {e}\nrun: pip install playwright pillow numpy && playwright install chromium", file=sys.stderr)
        return 2

    if not piece_ids:
        print("usage: validate_render.py <piece_id> [piece_id ...]", file=sys.stderr)
        return 2

    failed = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=[
            "--use-gl=swiftshader",
            "--enable-webgl",
            "--no-sandbox",
        ])
        ctx = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=2)
        for pid in piece_ids:
            piece_html = PIECES / pid / "index.html"
            if not piece_html.exists():
                print(f"  ✗ {pid}: no index.html")
                failed.append((pid, "missing"))
                continue
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            try:
                page.goto(f"file://{piece_html}", wait_until="load")
                page.wait_for_timeout(_warmup_for(pid))
                out = REPO / "thumbs" / f"{pid}.png"
                page.screenshot(path=str(out), type="png")

                # content check
                img = Image.open(out).convert("RGB")
                arr = np.array(img)
                # robust background estimate: median per channel
                bg = np.median(arr.reshape(-1, 3), axis=0)
                # any pixel that's >12 channel-units away from bg in L1 distance
                dist = np.abs(arr.astype(np.int32) - bg.astype(np.int32)).sum(axis=2)
                non_bg = (dist > 12).sum() / dist.size
                ok = non_bg >= MIN_NONBACKGROUND_FRACTION
                marker = "✓" if ok else "✗"
                print(f"  {marker} {pid}: non-background={non_bg*100:.2f}% (threshold {MIN_NONBACKGROUND_FRACTION*100:.1f}%)" +
                      (f"  errors: {errors[:2]}" if errors else ""))
                if not ok:
                    failed.append((pid, f"empty render ({non_bg*100:.2f}%)" + (f"; pageerror: {errors[0]}" if errors else "")))
            except Exception as e:
                print(f"  ✗ {pid}: render exception — {e}")
                failed.append((pid, str(e)))
            finally:
                page.close()
        ctx.close()
        browser.close()

    if failed:
        print()
        print(f"  {len(failed)} piece(s) failed validation:")
        for pid, reason in failed:
            print(f"    - {pid}: {reason}")
        return 1
    print()
    print(f"  all {len(piece_ids)} piece(s) passed")
    return 0


if __name__ == "__main__":
    sys.exit(validate(sys.argv[1:]))
