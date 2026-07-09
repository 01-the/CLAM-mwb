#!/usr/bin/env python3
"""
scrape_mwb.py

Pulls the current week's "Life and Ministry Meeting" schedule from the
Watchtower ONLINE LIBRARY (wol.jw.org) and turns it into compact JSON
suitable for a TRMNL polling plugin.

We deliberately only extract *schedule* information: section names,
part numbers/titles, assigned times, song numbers, and short reference
tags (e.g. "Jer 13:1-14"). This mirrors exactly what a congregation
posts on its information board -- not the full study content of each
part.

Usage:
    python scrape_mwb.py                     # this week (system local date)
    python scrape_mwb.py --date 2026-07-09   # week containing this date
    python scrape_mwb.py --out data/mwb.json # write to a specific path
"""

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, NavigableString

WOL_BASE = "https://wol.jw.org/en/wol/dt/r1/lp-e"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

SECTION_KEYWORDS = {
    "treasures from god": "Treasures From God's Word",
    "apply yourself to the field ministry": "Apply Yourself to the Field Ministry",
    "living as christians": "Living as Christians",
}

MONTH_NAMES = (
    "january|february|march|april|may|june|july|august|"
    "september|october|november|december"
)
WEEK_HEADING_RE = re.compile(MONTH_NAMES, re.IGNORECASE)
POINT_RE = re.compile(r"^(\d+)\.\s*(.+)$")
SONG_ONLY_RE = re.compile(r"^song\s+(\d+)$", re.IGNORECASE)
CONCLUDING_RE = re.compile(r"concluding comments", re.IGNORECASE)
TIME_RE = re.compile(r"^\(?\s*(\d+)\s*min\.?\)?\s*", re.IGNORECASE)
SONG_INLINE_RE = re.compile(r"song\s+(\d+)", re.IGNORECASE)

MAX_DETAIL_CHARS = 100


def fetch_week_html(target_date: date, attempts: int = 4) -> str:
    url = f"{WOL_BASE}/{target_date.year}/{target_date.month}/{target_date.day}"
    last_exc = None
    for i in range(attempts):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=45)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            last_exc = exc
            if i < attempts - 1:
                wait = 5 * (i + 1)
                print(
                    f"Request failed ({exc}); retrying in {wait}s "
                    f"({i + 1}/{attempts})...",
                    file=sys.stderr,
                )
                time.sleep(wait)
    raise last_exc


def clean_text(text: str) -> str:
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def truncate(text: str, limit: int = MAX_DETAIL_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",.;:") + "\u2026"


def is_boilerplate(text: str) -> bool:
    lowered = text.lower()
    return lowered in ("your answer",) or lowered.startswith(
        "what spiritual gems from this week"
    )


def parse_week(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")

    # Only walk semantic content tags, in document order. This is
    # resilient to wrapper <div> class names changing, since it only
    # depends on heading/paragraph tag structure.
    nodes = soup.find_all(["h1", "h2", "h3", "p", "li"])

    # Locate the week heading (h1 containing a month name). The dt page
    # can render other h1s (rare), so prefer the first match.
    start_idx = None
    for i, node in enumerate(nodes):
        if node.name == "h1" and WEEK_HEADING_RE.search(node.get_text()):
            start_idx = i
            break
    if start_idx is None:
        raise RuntimeError("Could not locate the week heading (h1) on the page")

    week_label = clean_text(nodes[start_idx].get_text())

    result = {
        "week_label": week_label,
        "bible_reading": None,
        "opening_song": None,
        "opening_time": None,
        "sections": [],
    }

    current_section = None
    current_point = None
    header_phase = True  # before the first recognized section heading

    for node in nodes[start_idx + 1 :]:
        text = clean_text(node.get_text())
        if not text:
            continue

        if node.name == "h1":
            break  # next week's block (shouldn't normally happen)

        if node.name == "h2":
            key = text.lower()
            matched_section = None
            for kw, label in SECTION_KEYWORDS.items():
                if kw in key:
                    matched_section = label
                    break
            if matched_section:
                header_phase = False
                current_section = {
                    "section": matched_section,
                    "song": None,
                    "items": [],
                    "concluding": None,
                }
                result["sections"].append(current_section)
                current_point = None
                continue
            elif header_phase and result["bible_reading"] is None:
                # e.g. "JEREMIAH 13-15"
                result["bible_reading"] = text.title()
                continue
            # unrecognized h2, ignore
            continue

        if node.name == "h3":
            if header_phase:
                # Opening song / prayer heading
                song_match = SONG_INLINE_RE.search(text)
                time_match = TIME_RE.search(text) or re.search(
                    r"\((\d+)\s*min", text
                )
                if song_match:
                    result["opening_song"] = song_match.group(1)
                if time_match:
                    result["opening_time"] = f"{time_match.group(1)} min."
                continue

            if current_section is None:
                continue

            if CONCLUDING_RE.search(text):
                song_match = SONG_INLINE_RE.search(text)
                time_match = re.search(r"\((\d+)\s*min", text)
                current_section["concluding"] = {
                    "time": f"{time_match.group(1)} min." if time_match else None,
                    "song": song_match.group(1) if song_match else None,
                }
                current_point = None
                continue

            point_match = POINT_RE.match(text)
            if point_match:
                current_point = {
                    "num": int(point_match.group(1)),
                    "title": clean_text(point_match.group(2)),
                    "time": None,
                    "detail": None,
                }
                current_section["items"].append(current_point)
                continue

            song_match = SONG_ONLY_RE.match(text)
            if song_match:
                current_section["song"] = song_match.group(1)
                current_point = None
                continue

            # Unrecognized h3 inside a section: ignore
            continue

        if node.name in ("p", "li"):
            if header_phase or current_point is None:
                continue
            if is_boilerplate(text):
                continue

            if current_point["time"] is None:
                time_match = TIME_RE.match(text)
                if time_match:
                    current_point["time"] = f"{time_match.group(1)} min."
                    remainder = clean_text(TIME_RE.sub("", text, count=1))
                    if remainder and not is_boilerplate(remainder):
                        current_point["detail"] = truncate(remainder)
                    continue
                # No duration found yet on a plain paragraph; treat as
                # detail candidate only if we still need one.
                if current_point["detail"] is None:
                    current_point["detail"] = truncate(text)
                continue

            if current_point["detail"] is None:
                current_point["detail"] = truncate(text)
                continue
            # Already have time + detail for this point; skip the rest
            # of its body content to keep the payload glanceable.
            continue

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--date",
        help="Date (YYYY-MM-DD) inside the target week. Defaults to today.",
    )
    parser.add_argument(
        "--tz",
        default="Australia/Sydney",
        help="IANA timezone used to determine 'today' (default: Australia/Sydney). "
        "Matters because CI runners default to UTC, which can be a different "
        "calendar day than Sydney.",
    )
    parser.add_argument(
        "--out",
        default="data/mwb.json",
        help="Output JSON path (default: data/mwb.json)",
    )
    args = parser.parse_args()

    if args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        target_date = datetime.now(ZoneInfo(args.tz)).date()

    html = fetch_week_html(target_date)
    data = parse_week(html)
    data["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data["source_url"] = (
        f"{WOL_BASE}/{target_date.year}/{target_date.month}/{target_date.day}"
    )

    import os

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Wrote {args.out}")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:2000])


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
