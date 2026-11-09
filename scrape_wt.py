#!/usr/bin/env python3
"""
scrape_wt.py

Python port of the "wt" Cloudflare Worker. Fetches THIS WEEK's Watchtower
Study article from wol.jw.org and writes JSON in exactly the same shape the
Worker returns, so the TRMNL templates need no changes.

Usage:
    python scrape_wt.py                    # writes data/wt.json
    python scrape_wt.py --out data/wt.json
    python scrape_wt.py --debug            # print first 20kb of article HTML

Behaviour differences from the Worker:
  * If the scrape fails, the previous JSON file is left untouched (the Worker
    returned has_data:false instead), so the display keeps the last good week.
  * The file is only rewritten when the content changes, so the git history
    isn't filled with timestamp-only commits.
"""

import argparse
import html as htmllib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

BASE = "https://wol.jw.org"
MEETINGS_URL = f"{BASE}/en/wol/meetings/r1/lp-e"  # "This Week" shortcut
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch(url: str, attempts: int = 3) -> str:
    last_exc = None
    for i in range(attempts):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=45)
            resp.raise_for_status()
            text = resp.text
            if "not a robot" in text.lower() and len(text) < 20000:
                raise RuntimeError(
                    f"Robot-check page returned for {url} (length {len(text)})"
                )
            return text
        except (requests.RequestException, RuntimeError) as exc:
            last_exc = exc
            if i < attempts - 1:
                wait = 5 * (i + 1)
                print(f"Fetch failed ({exc}); retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
    raise last_exc


def decode_entities(s: str) -> str:
    return htmllib.unescape(s).replace("\xa0", " ")


def strip_tags(s) -> str:
    if not s:
        return ""
    no_tags = re.sub(r"<[^>]+>", "", s)
    return decode_entities(re.sub(r"\s+", " ", no_tags).strip())


def get_article_url() -> str:
    html = fetch(MEETINGS_URL)
    parts = re.split(r"Watchtower Study", html, flags=re.IGNORECASE)
    if len(parts) < 2:
        raise RuntimeError("Could not locate 'Watchtower Study' section on meetings page")
    m = re.search(r'href="([^"]+/wol/d/[^"]+)"', parts[1], re.IGNORECASE)
    if not m:
        raise RuntimeError("Could not find Watchtower Study article link")
    href = m.group(1)
    if href.startswith("/"):
        href = BASE + href
    return href


def first_paragraph(html: str):
    m = re.search(r"<p[^>]*>([\s\S]*?)</p>", html, re.IGNORECASE)
    return strip_tags(m.group(1)) if m else ""


def parse_article(html: str, url: str) -> dict:
    # Opening and closing songs: "SONG 133</a> Worship Jehovah During Youth"
    songs = re.findall(r">SONG\s+(\d+)</a>\s*([^<]+)", html, re.IGNORECASE)
    opening_song = f"Song {songs[0][0]} \u2013 {strip_tags(songs[0][1])}" if songs else None
    closing_song = (
        f"Song {songs[-1][0]} \u2013 {strip_tags(songs[-1][1])}"
        if len(songs) > 1
        else None
    )

    # Week date range, e.g. "JULY 20-26, 2026"
    dm = re.search(
        r">([A-Z][A-Z\u2013\u2014\s]+\d{1,2}(?:[\u2013\u2014-]\d{1,2})?,\s*\d{4})</(p|strong|span)>",
        html,
    )
    week_range = strip_tags(dm.group(1)) if dm else ""

    # Title (h1) and theme scripture (first <p> after it)
    tm = re.search(r"<h1[^>]*>([\s\S]*?)</h1>", html, re.IGNORECASE)
    title = strip_tags(tm.group(1)) if tm else ""
    theme = first_paragraph(html[tm.end():]) if tm else ""

    # FOCUS line
    focus = ""
    fm = re.search(r">FOCUS</", html, re.IGNORECASE)
    if fm:
        focus = first_paragraph(html[fm.start():])

    # Subheadings (h2), excluding the boxed titles
    excluded = {"bible principles to consider", "how would you answer?", "how would you answer"}
    subheadings = []
    for hm in re.finditer(r"<h2[^>]*>([\s\S]*?)</h2>", html, re.IGNORECASE):
        text = strip_tags(hm.group(1))
        if text and text.lower() not in excluded:
            subheadings.append(text)

    # Review questions: the "HOW WOULD YOU ANSWER?" box
    review = []
    rm = re.search(r"HOW WOULD YOU ANSWER", html, re.IGNORECASE)
    if rm:
        ul = re.search(r"<ul[^>]*>([\s\S]*?)</ul>", html[rm.start():], re.IGNORECASE)
        if ul:
            for lm in re.finditer(r"<li[^>]*>([\s\S]*?)</li>", ul.group(1), re.IGNORECASE):
                text = strip_tags(lm.group(1))
                if text:
                    review.append(text)

    return {
        "has_data": bool(title and subheadings),
        "week_range": week_range,
        "opening_song": opening_song,
        "title": title,
        "theme": theme,
        "focus": focus,
        "subheadings": subheadings,
        "review_questions": review,
        "closing_song": closing_song,
        "source_url": url,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }


def same_content(path: str, new: dict) -> bool:
    try:
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        return False
    strip = lambda d: {k: v for k, v in d.items() if k != "fetched_at"}
    return strip(old) == strip(new)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data/wt.json")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    article_url = get_article_url()

    if args.debug:
        print(fetch(article_url)[:20000])
        return

    data = parse_article(fetch(article_url), article_url)
    if not data["has_data"]:
        raise RuntimeError(
            "Article fetched but no title/subheadings found "
            "(page structure may have changed; try --debug)"
        )

    if same_content(args.out, data):
        print(f"No change in {args.out}")
        return

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Wrote {args.out}")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:1500])


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
