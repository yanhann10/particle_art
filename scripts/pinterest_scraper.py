#!/usr/bin/env python3
"""Playwright-based Pinterest board scraper.

Scrapes a public Pinterest board and saves pin data to pinterest_pins.json.
No OAuth required for public boards; falls back to Pinterest internal JSON
API before launching Playwright.

Usage:
    python scripts/pinterest_scraper.py [--board-url URL] [--limit N] [--output PATH]
"""
import argparse
import json
import ssl
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()

REPO            = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT  = REPO / "scripts" / "pinterest_pins.json"
DEFAULT_COOKIES = REPO / "scripts" / "pinterest_cookies.json"
DEFAULT_BOARD   = "https://www.pinterest.com/hannahyan/particle-art/"


# ── Pinterest internal JSON API (no auth, works for public boards) ──────────

def _board_slug(board_url: str) -> tuple[str, str]:
    """Return (username, board_slug) from a board URL."""
    parts = [p for p in urllib.parse.urlparse(board_url).path.strip("/").split("/") if p]
    if len(parts) < 2:
        return "", ""
    return parts[0], parts[1]


def fetch_via_api(board_url: str, limit: int) -> list[dict]:
    """Try Pinterest's public board API. Returns [] on failure."""
    username, slug = _board_slug(board_url)
    if not username or not slug:
        return []

    # Pinterest's mobile/widget API — works for public boards without auth
    api_url = (
        "https://www.pinterest.com/resource/BoardFeedResource/get/"
        f"?source_url=/{username}/{slug}/&"
        "data=%7B%22options%22%3A%7B%22board_url%22%3A%22%2F"
        f"{username}%2F{slug}%2F%22%2C%22page_size%22%3A{min(limit, 50)}"
        "%7D%2C%22context%22%3A%7B%7D%7D"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": board_url,
    }
    try:
        req = urllib.request.Request(api_url, headers=headers)
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=15) as r:
            raw = json.loads(r.read())
        items = (raw.get("resource_response") or {}).get("data") or []
        return _normalize_api_pins(items[:limit])
    except Exception as e:
        print(f"  API fetch failed ({e}), falling back to Playwright")
        return []


def _normalize_api_pins(items: list[dict]) -> list[dict]:
    pins = []
    for item in items:
        if not isinstance(item, dict):
            continue
        pin_id = str(item.get("id") or "")
        images = item.get("images") or {}
        img_url = (
            (images.get("736x") or images.get("474x") or images.get("236x") or {}).get("url") or
            next((v.get("url") for v in images.values() if isinstance(v, dict) and v.get("url")), "")
        )
        pins.append({
            "id":          pin_id,
            "url":         f"https://www.pinterest.com/pin/{pin_id}/",
            "image_url":   img_url,
            "description": (item.get("title") or item.get("description") or "")[:300],
        })
    return [p for p in pins if p["image_url"]]


# ── Playwright scraper (fallback) ─────────────────────────────────────────────

def _upgrade_img_url(url: str) -> str:
    for size in ("/236x/", "/474x/", "/170x/"):
        if size in url:
            return url.replace(size, "/736x/")
    return url


def _load_cookies(cookies_path: Path) -> list[dict]:
    if cookies_path.exists():
        try:
            return json.loads(cookies_path.read_text())
        except Exception:
            pass
    return []


def scrape_via_playwright(board_url: str, limit: int,
                          cookies_path: Path = DEFAULT_COOKIES) -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit(
            "playwright not installed.\n"
            "  pip install playwright && playwright install chromium"
        )

    pins   = []
    seen   = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
        )

        # Load saved Pinterest cookies if available
        cookies = _load_cookies(cookies_path)
        if cookies:
            ctx.add_cookies(cookies)
            print(f"  Loaded {len(cookies)} Pinterest session cookies")
        else:
            print(
                f"  No cookies found at {cookies_path}\n"
                "  Run: python scripts/setup_pinterest_cookies.py"
            )

        page = ctx.new_page()

        print(f"  Playwright → {board_url}")
        try:
            page.goto(board_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"  Navigation error: {e}")
            browser.close()
            return []

        time.sleep(3)

        # Detect login redirect — board needs auth
        if "/login" in page.url or "/ideas" in page.url or "/auth" in page.url:
            print(
                "  Redirected — board needs auth.\n"
                "  Run: python scripts/setup_pinterest_cookies.py"
            )
            browser.close()
            return []

        # Wait for pin containers (multiple selector strategies)
        pin_selector = None
        for sel in ["[data-test-id='pin']", "[data-grid-item]", "div[role='listitem'] a[href*='/pin/']"]:
            try:
                page.wait_for_selector(sel, timeout=8000)
                pin_selector = sel
                break
            except Exception:
                continue

        if not pin_selector:
            print("  Could not locate pin elements")
            browser.close()
            return []

        # Scroll to load up to `limit` pins
        for _ in range(max(limit // 10, 3)):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(1.5)
            count = len(page.query_selector_all(pin_selector))
            if count >= limit:
                break

        # Extract pins
        for elem in page.query_selector_all(pin_selector)[:limit]:
            try:
                link = elem if elem.get_attribute("href") else elem.query_selector("a")
                href = (link.get_attribute("href") or "") if link else ""
                pin_id = href.split("/pin/")[1].strip("/") if "/pin/" in href else ""
                if not pin_id or pin_id in seen:
                    continue
                seen.add(pin_id)

                img = elem.query_selector("img")
                img_url = ""
                if img:
                    img_url = (
                        img.get_attribute("src") or
                        img.get_attribute("data-src") or ""
                    )
                    img_url = _upgrade_img_url(img_url)

                alt = img.get_attribute("alt") if img else ""

                pins.append({
                    "id":          pin_id,
                    "url":         f"https://www.pinterest.com{href}" if href.startswith("/") else href,
                    "image_url":   img_url,
                    "description": (alt or "")[:300],
                })
            except Exception:
                continue

        browser.close()

    return [p for p in pins if p["image_url"]]


# ── Main ──────────────────────────────────────────────────────────────────────

def scrape_board(board_url: str, limit: int,
                 cookies_path: Path = DEFAULT_COOKIES) -> list[dict]:
    print(f"Step 1 — Pinterest JSON API…")
    pins = fetch_via_api(board_url, limit)
    if pins:
        print(f"  API returned {len(pins)} pins")
        return pins

    print(f"Step 1 — Playwright fallback…")
    pins = scrape_via_playwright(board_url, limit, cookies_path)
    print(f"  Playwright returned {len(pins)} pins")
    return pins


def main():
    ap = argparse.ArgumentParser(description="Pinterest board scraper")
    ap.add_argument("--board-url", default=DEFAULT_BOARD)
    ap.add_argument("--limit",     type=int, default=30)
    ap.add_argument("--output",    default=str(DEFAULT_OUTPUT))
    ap.add_argument("--cookies",   default=str(DEFAULT_COOKIES),
                    help="Path to Pinterest session cookies JSON")
    args = ap.parse_args()

    pins = scrape_board(args.board_url, args.limit, Path(args.cookies))
    if not pins:
        print(
            "No pins scraped.\n"
            "If the board is private: run python scripts/setup_pinterest_cookies.py first."
        )
        sys.exit(1)

    out = Path(args.output)
    out.write_text(json.dumps(pins, indent=2))
    print(f"Saved {len(pins)} pins → {out}")


if __name__ == "__main__":
    main()
