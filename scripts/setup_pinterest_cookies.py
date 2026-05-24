#!/usr/bin/env python3
"""Interactive Pinterest session cookie setup.

Opens a HEADED Chrome window pointed at Pinterest login.
Detects login by watching for the '_auth' Pinterest cookie to appear
(works with Google OAuth, Apple, or email/password — no URL-watching).

Run once. Re-run if cookies expire (Pinterest sessions last ~1 year).

Usage:
    python scripts/setup_pinterest_cookies.py [--timeout 300] [--output PATH]
"""
import argparse
import json
import sys
import time
from pathlib import Path

REPO           = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = REPO / "scripts" / "pinterest_cookies.json"
BOARD_URL      = "https://www.pinterest.com/hannahyan/particle-art/"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output",  default=str(DEFAULT_OUTPUT))
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("playwright not installed: pip install playwright && playwright install chromium")

    out = Path(args.output)

    print("=" * 60)
    print("Pinterest Cookie Setup")
    print("=" * 60)
    print("Chrome will open. Log in with Google, Apple, or email.")
    print("Cookies are saved automatically when login is detected.")
    print(f"Timeout: {args.timeout}s\n")

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                channel="chrome",
                headless=False,
                args=["--no-first-run", "--no-default-browser-check"],
            )
            print("Using: Google Chrome")
        except Exception as e:
            print(f"Chrome not found ({e}), using Playwright Chromium.")
            browser = p.chromium.launch(headless=False)

        ctx  = browser.new_context(viewport={"width": 1200, "height": 800})
        page = ctx.new_page()
        page.goto("https://www.pinterest.com/login/", wait_until="domcontentloaded")
        print("Browser open. Log in now (Google, Apple, or email/password).\n")

        deadline  = time.time() + args.timeout
        logged_in = False

        while time.time() < deadline:
            time.sleep(2)

            # Detect login via the Pinterest _auth cookie appearing
            try:
                cookies = ctx.cookies()
            except Exception:
                break

            pin_cookies = {c["name"]: c for c in cookies
                           if "pinterest" in c.get("domain", "")}

            auth_val = pin_cookies.get("_auth", {}).get("value", "0")
            # _auth=0 means not yet logged in; real token is 50+ chars
            if len(auth_val) > 10:
                print(f"✓ Pinterest _auth token present (len={len(auth_val)}) — logged in!")
                logged_in = True
                break

            remaining = int(deadline - time.time())
            auth_preview = f"_auth={auth_val[:6]!r}(len={len(auth_val)})"
            print(f"  ({remaining}s left) {auth_preview}  total pin cookies={len(pin_cookies)}")

        if not logged_in:
            print("\n✗ Timed out without detecting login.")
            print("  Re-run and complete the login before the timeout.")
            browser.close()
            sys.exit(1)

        time.sleep(1)
        cookies = ctx.cookies()
        out.write_text(json.dumps(cookies, indent=2))
        n_pin = sum(1 for c in cookies if "pinterest" in c.get("domain", ""))
        print(f"✓ Saved {len(cookies)} cookies ({n_pin} pinterest-domain) → {out}")

        # Quick board check
        try:
            page.goto(BOARD_URL, wait_until="domcontentloaded", timeout=20000)
            time.sleep(2)
            url = page.url
            if "particle-art" in url:
                print(f"✓ Board accessible: {url}")
            else:
                print(f"  Board redirected to: {url}")
                print("  Board may be private — check board visibility in Pinterest settings.")
        except Exception as e:
            print(f"  Board check skipped: {e}")

        browser.close()

    print("\nDone. Run the pipeline:")
    print("  cd /Users/hanyan/git_repo/particle_art")
    print("  doppler run --project ai-api --config dev -- \\")
    print("    python3 scripts/run_pinterest_pipeline.py --limit 20")


if __name__ == "__main__":
    main()
