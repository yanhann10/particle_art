#!/usr/bin/env python3
"""Capture the DYNAMIC loop of pieces as short videos (issue #13).

The static thumbnail pipeline (render_thumbs.py / validate_render.py) sees one
frame; the aesthetic judge needs motion. This records ~8s of each piece after
warmup as a webm via Playwright's context-level video recorder — the same
recording machinery validate_render.py uses for its 3s debug clips, batched
and parallelized.

Usage:
    scripts/capture_loop.py xs4 5gm                       # capture two pieces
    scripts/capture_loop.py --ids-file ids.txt --workers 4
    scripts/capture_loop.py --pieces-dir /tmp/pa_eval/drops --out-dir /tmp/pa_eval/clips a b c
"""
import argparse
import json
import sys
from multiprocessing import Pool
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

W, H = 640, 400          # small on purpose: VLM video tokens scale with pixels
DEFAULT_WARMUP_MS = 2500
DEFAULT_SECONDS = 8.0


def _warmup_for(pieces_dir: Path, pid: str) -> int:
    meta = pieces_dir / pid / "meta.json"
    if meta.exists():
        try:
            return int(json.loads(meta.read_text()).get("warmup_ms", DEFAULT_WARMUP_MS))
        except Exception:
            pass
    return DEFAULT_WARMUP_MS


def capture_batch(job: tuple) -> list:
    """One worker process: own browser, sequential captures of its slice."""
    ids, pieces_dir, out_dir, seconds, force = job
    pieces_dir, out_dir = Path(pieces_dir), Path(out_dir)
    from playwright.sync_api import sync_playwright

    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=[
            "--use-gl=swiftshader", "--enable-webgl", "--no-sandbox",
        ])
        for pid in ids:
            target = out_dir / f"{pid}.webm"
            if target.exists() and not force:
                results.append((pid, "cached"))
                continue
            html = pieces_dir / pid / "index.html"
            if not html.exists():
                results.append((pid, "missing"))
                continue
            ctx = browser.new_context(
                viewport={"width": W, "height": H},
                record_video_dir=str(out_dir),
                record_video_size={"width": W, "height": H},
            )
            page = ctx.new_page()
            try:
                page.goto(f"file://{html}", wait_until="load", timeout=60000)
                # hide piece UI chrome (GRADE panel, steer buttons, hint text) so
                # the judge sees only the artwork — anything that is not a canvas
                # and does not contain one
                page.evaluate(
                    "[...document.body.querySelectorAll('*')].forEach(el => {"
                    "  if (el.tagName !== 'CANVAS' && !el.querySelector('canvas'))"
                    "    el.style.visibility = 'hidden';"
                    "})"
                )
                page.wait_for_timeout(_warmup_for(pieces_dir, pid))
                # restart recording cleanly is not possible mid-context; instead
                # we record warmup too and trim it off with ffmpeg downstream if
                # needed — judges are told the first moments may be initialization
                page.wait_for_timeout(int(seconds * 1000))
                video = page.video
                page.close()
                ctx.close()
                if video is not None:
                    video.save_as(str(target))
                    video.delete()
                    results.append((pid, "ok"))
                else:
                    results.append((pid, "no-video"))
            except Exception as e:
                try:
                    page.close(); ctx.close()
                except Exception:
                    pass
                results.append((pid, f"error: {e}"))
        browser.close()
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--ids-file", type=Path)
    ap.add_argument("--pieces-dir", type=Path, default=REPO / "pieces")
    ap.add_argument("--out-dir", type=Path, default=REPO / "clips_judge")
    ap.add_argument("--seconds", type=float, default=DEFAULT_SECONDS)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true", help="re-capture cached clips")
    args = ap.parse_args()

    ids = list(args.ids)
    if args.ids_file:
        ids += [l.strip() for l in args.ids_file.read_text().splitlines() if l.strip()]
    if not ids:
        ap.error("no piece ids given")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    n = max(1, min(args.workers, len(ids)))
    chunks = [ids[i::n] for i in range(n)]
    jobs = [(c, str(args.pieces_dir), str(args.out_dir), args.seconds, args.force)
            for c in chunks if c]
    with Pool(len(jobs)) as pool:
        all_results = [r for batch in pool.map(capture_batch, jobs) for r in batch]

    ok = sum(1 for _, s in all_results if s in ("ok", "cached"))
    bad = [(p, s) for p, s in all_results if s not in ("ok", "cached")]
    print(f"{ok}/{len(ids)} captured → {args.out_dir}")
    for p, s in bad:
        print(f"  ✗ {p}: {s}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
