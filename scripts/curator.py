#!/usr/bin/env python3
"""Art curator agent — fetches RSS from top art publications, distills generative art directions.

Usage:
    python3 scripts/curator.py            # live run
    python3 scripts/curator.py --dry-run  # print directions without writing
    python3 scripts/curator.py --limit 3  # max 3 articles per feed
"""
import argparse, json, sys, xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).parent))
from lib_claude import call

SCRIPTS_DIR    = Path(__file__).parent
KNOWLEDGE_DIR  = SCRIPTS_DIR / "knowledge"
SEEN_FILE      = KNOWLEDGE_DIR / "curator_seen.txt"
FEED_FILE      = KNOWLEDGE_DIR / "curator_feed.jsonl"
DIRECTIVES_FILE = SCRIPTS_DIR / "pending_directives.jsonl"
ATOM           = "http://www.w3.org/2005/Atom"

RSS_SOURCES = [
    "https://hyperallergic.com/rss/",         # replaces e-flux /rss/ (404)
    "https://www.artforum.com/feed/",
    "https://www.artnews.com/feed/",
]
SYSTEM_PROMPT = (
    "You are an art direction filter. Given an article title and summary from an art "
    "publication, decide if it contains any aesthetic or technique ideas usable in "
    "generative particle art. If yes, return a single terse creative direction "
    "(≤ 25 words, imperative). If not relevant, return exactly: SKIP"
)


def fetch_articles(url: str, limit: int) -> list[dict]:
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "curator-agent/1.0"})
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as e:
        print(f"[curator] error {url}: {e}", file=sys.stderr)
        return []

    arts = []
    for item in root.iter("item"):                         # RSS 2.0
        link = (item.findtext("link") or "").strip()
        if link:
            arts.append({"title": (item.findtext("title") or "").strip(), "url": link,
                         "summary": (item.findtext("description") or "").strip()})
        if len(arts) >= limit: break

    for entry in (root.iter(f"{{{ATOM}}}entry") if not arts else []):   # Atom
        lel = entry.find(f"{{{ATOM}}}link")
        link = (lel.get("href") if lel is not None else "") or ""
        if link:
            arts.append({"title": (entry.findtext(f"{{{ATOM}}}title") or "").strip(), "url": link,
                         "summary": (entry.findtext(f"{{{ATOM}}}summary") or "").strip()})
        if len(arts) >= limit: break

    return arts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=5)
    args = ap.parse_args()

    if not args.dry_run:
        KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)

    seen = set(SEEN_FILE.read_text().splitlines()) if SEEN_FILE.exists() else set()
    new_seen: list[str] = []
    now = datetime.now(timezone.utc).isoformat()

    for src in RSS_SOURCES:
        for art in fetch_articles(src, args.limit):
            url = art["url"]
            if url in seen:
                continue
            direction, _ = call(SYSTEM_PROMPT, f"{art['title']}\n\n{art['summary'][:500]}")
            direction = direction.strip()
            new_seen.append(url)
            if direction.upper() == "SKIP" or not direction:
                continue
            print(f"[curator] {art['title'][:60]}\n  → {direction}\n")
            if not args.dry_run:
                with open(FEED_FILE, "a") as f:
                    f.write(json.dumps({"source": "curator", "url": url, "title": art["title"],
                                        "direction": direction, "ts": now}) + "\n")
                with open(DIRECTIVES_FILE, "a") as f:
                    f.write(json.dumps({"source": "curator", "priority_directive": direction,
                                        "queued_at": now}) + "\n")

    if not args.dry_run and new_seen:
        with open(SEEN_FILE, "a") as f:
            f.write("\n".join(new_seen) + "\n")

    print(f"[curator] done — {len(new_seen)} articles processed")


if __name__ == "__main__":
    main()
