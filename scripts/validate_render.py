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

# What counts as "broken"? If <1% of pixels are >12 channel-units away
# from the median (background) color, the piece is rendering essentially
# nothing — typically a shader compile failure, missing model load,
# or a design that waits for an input that never arrives in headless.
#
# Tuned 2026-05-07 from 0.5% → 1.0% based on coverage distribution over
# 106 existing pieces (median=12.75%, p25=4.58%). At 1%, we cull 11/106
# (10%) of pieces — exactly the pieces the user flagged as "don't show"
# (ytq/ww9/t6y/93o etc. at <0.1%). At 0.5% the gate was passing pieces
# the user perceived as empty.
MIN_NONBACKGROUND_FRACTION = 0.01

# CONTRAST GATE — user 2026-05-07: many pieces pass the non-bg threshold yet
# the eye reads them as "empty / faint / cant see / blurry / low-contrast"
# (jwu, kjn, 7wq, si0, usm flagged). Reject when grayscale stddev is too
# small (image is mostly one shade) or dynamic range of luminance is too
# narrow (no real foreground/background separation).
# Range uses p99-p1 (not p95-p5): a bright crisp subject on a dark ground
# is fully visible but may cover <5% of pixels, so p95 would still sit in the
# dark majority and wrongly read range~0. p99-p1 lets a subject covering ≳1%
# register, so dark backgrounds pass while globally-faint images still fail.
MIN_GRAYSCALE_STDDEV   = 12.0   # 0..255 scale
MIN_LUMA_DYNAMIC_RANGE = 35.0   # p99 - p1 of luminance, 0..255 scale
# BLUR GATE — Laplacian variance measures edge sharpness. Blurry renders (feathered
# sprites, Gaussian-smeared particles, soft halos drowning the form) score near zero;
# sharp renders score 50+. Pieces like si0/7wq/610 that the user flagged as "blurry"
# would fail this gate. Tunable via env PARTICLE_ART_MIN_SHARPNESS.
#
# Calibration 2026-05-24: raised from 30 → 150 after r5f (sharpness=85) was
# flagged as blurry despite clearing the old gate. Reference scores post-fix:
# zv4=3661, h80=1140, kam=976, p9l=215. sav=49.9 is a pre-gate legacy piece.
MIN_SHARPNESS_VARIANCE = float(
    __import__("os").environ.get("PARTICLE_ART_MIN_SHARPNESS", "150.0")
)

W, H = 800, 500


def _warmup_for(piece_id: str) -> int:
    meta_path = PIECES / piece_id / "meta.json"
    if not meta_path.exists():
        return 5000
    try:
        m = json.loads(meta_path.read_text())
    except Exception:
        return 5000
    if isinstance(m.get("warmup_ms"), (int, float)):
        return int(m["warmup_ms"])
    if m.get("model_source") or any("Loader" in s for s in m.get("stack", [])):
        return 9000
    return 5000


def validate(piece_ids: list[str], record_clip: bool = False,
             return_details: bool = False) -> "int | tuple[int, list]":
    """If record_clip is True, capture a 3-second webm motion clip per piece in addition to the still PNG."""
    try:
        from playwright.sync_api import sync_playwright
        from PIL import Image
        import numpy as np
    except ImportError as e:
        print(f"missing dependency: {e}\nrun: pip install playwright pillow numpy && playwright install chromium", file=sys.stderr)
        return (2, []) if return_details else 2

    if not piece_ids:
        print("usage: validate_render.py <piece_id> [piece_id ...]", file=sys.stderr)
        return (2, []) if return_details else 2

    failed = []
    clips_dir = REPO / "clips"
    if record_clip:
        clips_dir.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=[
            "--use-gl=swiftshader",
            "--enable-webgl",
            "--no-sandbox",
        ])
        for pid in piece_ids:
            piece_html = PIECES / pid / "index.html"
            if not piece_html.exists():
                print(f"  ✗ {pid}: no index.html")
                failed.append((pid, "missing"))
                continue

            # per-piece context — needed because video recording is set at context level
            ctx_kwargs = {
                "viewport": {"width": W, "height": H},
                "device_scale_factor": 2,
            }
            if record_clip:
                ctx_kwargs["record_video_dir"] = str(clips_dir)
                ctx_kwargs["record_video_size"] = {"width": W, "height": H}
            ctx = browser.new_context(**ctx_kwargs)
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            try:
                page.goto(f"file://{piece_html}", wait_until="load", timeout=60000)
                page.wait_for_timeout(_warmup_for(pid))
                out = REPO / "thumbs" / f"{pid}.png"
                # heavy installations (Shiota-channel = 50k line segments etc.)
                # need more than the 30s default screenshot timeout
                page.screenshot(path=str(out), type="png", timeout=60000)
                if record_clip:
                    # additional 3s of motion capture after warmup
                    page.wait_for_timeout(3000)

                # content check
                img = Image.open(out).convert("RGB")
                arr = np.array(img)
                # robust background estimate: median per channel
                bg = np.median(arr.reshape(-1, 3), axis=0)
                # any pixel that's >12 channel-units away from bg in L1 distance
                dist = np.abs(arr.astype(np.int32) - bg.astype(np.int32)).sum(axis=2)
                non_bg = (dist > 12).sum() / dist.size

                # contrast gate — reject low-stddev / narrow-range renders
                gray = arr.astype(np.float32) @ np.array([0.299, 0.587, 0.114])
                gstd = float(gray.std())
                p1, p99 = np.percentile(gray, [1, 99])
                drange = float(p99 - p1)

                # blur gate — Laplacian variance: low = blurry/soft, high = sharp
                lap = (gray[2:, 1:-1] - 2 * gray[1:-1, 1:-1] + gray[:-2, 1:-1]
                       + gray[1:-1, 2:] - 2 * gray[1:-1, 1:-1] + gray[1:-1, :-2])
                sharpness = float(lap.var())

                fail_reason = None
                if non_bg < MIN_NONBACKGROUND_FRACTION:
                    fail_reason = f"empty render ({non_bg*100:.2f}% non-bg)"
                elif gstd < MIN_GRAYSCALE_STDDEV:
                    fail_reason = f"low-contrast (stddev={gstd:.1f}<{MIN_GRAYSCALE_STDDEV:.0f})"
                elif drange < MIN_LUMA_DYNAMIC_RANGE:
                    fail_reason = f"narrow dynamic range (p95-p5={drange:.1f}<{MIN_LUMA_DYNAMIC_RANGE:.0f})"
                elif sharpness < MIN_SHARPNESS_VARIANCE:
                    fail_reason = f"blurry (sharpness={sharpness:.1f}<{MIN_SHARPNESS_VARIANCE:.0f})"

                ok = fail_reason is None
                marker = "✓" if ok else "✗"
                print(f"  {marker} {pid}: non-bg={non_bg*100:.2f}% stddev={gstd:.1f} range={drange:.1f} sharpness={sharpness:.1f}" +
                      (f"  errors: {errors[:2]}" if errors else ""))
                if not ok:
                    failed.append((pid, fail_reason + (f"; pageerror: {errors[0]}" if errors else "")))
            except Exception as e:
                print(f"  ✗ {pid}: render exception — {e}")
                failed.append((pid, str(e)))
            finally:
                # finalize video before closing context (Playwright finalizes on context close)
                video = page.video
                page.close()
                ctx.close()
                if record_clip and video is not None:
                    try:
                        target = clips_dir / f"{pid}.webm"
                        video.save_as(str(target))
                        video.delete()
                        print(f"     clip saved: {target.relative_to(REPO)}")
                    except Exception as e:
                        print(f"     clip save failed: {e}")
        browser.close()

    if failed:
        print()
        print(f"  {len(failed)} piece(s) failed validation:")
        for pid, reason in failed:
            print(f"    - {pid}: {reason}")
        rc = 1
    else:
        print()
        print(f"  all {len(piece_ids)} piece(s) passed")
        rc = 0
    return (rc, failed) if return_details else rc


def validate_html_path(html_path: Path, label: str, warmup_ms: int = 2500,
                       thumb_path: Path | None = None) -> tuple[bool, str]:
    """Render an arbitrary HTML file headless and check it paints content.

    Used by theatrical_tick.py whose movements live at
    pieces/<target>/movements/m<N>/index.html — outside the
    pieces/<id>/index.html convention validate() expects.

    Returns (ok, reason). ok=True on pass; reason carries the % or error.
    Writes the screenshot to thumb_path if given (else discarded after check).
    """
    try:
        from playwright.sync_api import sync_playwright
        from PIL import Image
        import numpy as np
    except ImportError as e:
        return (True, f"skipped (missing dep: {e})")

    if not html_path.exists():
        return (False, f"no html at {html_path}")

    import tempfile
    use_temp = thumb_path is None
    with tempfile.TemporaryDirectory() as td:
        out = thumb_path if thumb_path else (Path(td) / f"{label}.png")
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(args=[
                    "--use-gl=swiftshader", "--enable-webgl", "--no-sandbox",
                ])
                ctx = browser.new_context(
                    viewport={"width": W, "height": H},
                    device_scale_factor=2,
                )
                page = ctx.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                try:
                    page.goto(f"file://{html_path}", wait_until="load", timeout=60000)
                    page.wait_for_timeout(warmup_ms)
                    page.screenshot(path=str(out), type="png", timeout=60000)
                    img = Image.open(out).convert("RGB")
                    arr = np.array(img)
                    bg = np.median(arr.reshape(-1, 3), axis=0)
                    dist = np.abs(arr.astype(np.int32) - bg.astype(np.int32)).sum(axis=2)
                    non_bg = (dist > 12).sum() / dist.size
                    ok = non_bg >= MIN_NONBACKGROUND_FRACTION
                    msg = f"non-background={non_bg*100:.2f}%"
                    if errors:
                        msg += f" pageerror={errors[0][:120]}"
                    return (bool(ok), msg)
                finally:
                    page.close()
                    ctx.close()
                    browser.close()
        except Exception as e:
            return (False, f"render exception: {e}")


if __name__ == "__main__":
    args = sys.argv[1:]
    record_clip = False
    if "--clip" in args:
        record_clip = True
        args = [a for a in args if a != "--clip"]
    sys.exit(validate(args, record_clip=record_clip))
