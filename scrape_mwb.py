#!/usr/bin/env python3
"""
scrape_mwb.py

Pulls the current week's "Life and Ministry Meeting" schedule and turns it
into compact JSON suitable for a TRMNL polling plugin.

Only *schedule* information is extracted: section names, part numbers and
titles, assigned times, song numbers, and short reference tags. This mirrors
what a congregation posts on its information board.

Sources, tried in order:
  1. wol.jw.org  (date-based weekly page)
  2. jw.org      (weekly schedule article)

If both fail, the error message includes what the page actually contained,
so the cause is visible in the GitHub Actions log.

Usage:
    python scrape_mwb.py                     # this week (Sydney date)
    python scrape_mwb.py --date 2026-11-05   # week containing this date
    python scrape_mwb.py --out data/mwb.json
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

WOL_BASE = "https://wol.jw.org/en/wol/dt/r1/lp-e"
JW_BASE = "https://www.jw.org/en/library/jw-meeting-workbook"

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

MONTHS = [
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
]
MONTH_PATTERN = "|".join(MONTHS)

# Workbook issues are bimonthly, named by the month the week STARTS in.
ISSUE_SLUGS = {
    1: "january-february", 2: "january-february",
    3: "march-april", 4: "march-april",
    5: "may-june", 6: "may-june",
    7: "july-august", 8: "july-august",
    9: "september-october", 10: "september-october",
    11: "november-december", 12: "november-december",
}

SECTION_KEYWORDS = {
    "treasures from god": "Treasures From God's Word",
    "apply yourself to the field ministry": "Apply Yourself to the Field Ministry",
    "living as christians": "Living as Christians",
}

# A week heading looks like "NOVEMBER 2-8" or "OCTOBER 27-NOVEMBER 2".
WEEK_RANGE_RE = re.compile(
    rf"^\s*(?:{MONTH_PATTERN})\s+\d{{1,2}}\s*[\u2013\u2014\-]\s*"
    rf"(?:(?:{MONTH_PATTERN})\s+)?\d{{1,2}}\s*$",
    re.IGNORECASE,
)
POINT_RE = re.compile(r"^(\d+)\.\s*(.+)$")
SONG_ONLY_RE = re.compile(r"^song\s+(\d+)$", re.IGNORECASE)
CONCLUDING_RE = re.compile(r"concluding comments", re.IGNORECASE)
TIME_RE = re.compile(r"^\(?\s*(\d+)\s*min\.?\)?\s*", re.IGNORECASE)
SONG_INLINE_RE = re.compile(r"song\s+(\d+)", re.IGNORECASE)

MAX_DETAIL_CHARS = 100


# ----------------------------------------------------------------- fetching


def week_bounds(target_date: date):
    monday = target_date - timedelta(days=target_date.weekday())
    return monday, monday + timedelta(days=6)


def wol_url(target_date: date) -> str:
    return f"{WOL_BASE}/{target_date.year}/{target_date.month}/{target_date.day}"


def jw_org_url(target_date: date) -> str:
    monday, sunday = week_bounds(target_date)
    if monday.month == sunday.month:
        span = f"{MONTHS[monday.month - 1]}-{monday.day}-{sunday.day}"
    else:
        span = (
            f"{MONTHS[monday.month - 1]}-{monday.day}-"
            f"{MONTHS[sunday.month - 1]}-{sunday.day}"
        )
    issue = f"{ISSUE_SLUGS[monday.month]}-{monday.year}-mwb"
    return (
        f"{JW_BASE}/{issue}/"
        f"Life-and-Ministry-Meeting-Schedule-for-{span}-{monday.year}/"
    )


def fetch_html(url: str, attempts: int = 3) -> str:
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


# ------------------------------------------------------------------ parsing


def clean_text(text: str) -> str:
    text = text.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


def truncate(text: str, limit: int = MAX_DETAIL_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",.;:") + "\u2026"


def is_boilerplate(text: str) -> bool:
    lowered = text.lower()
    return lowered == "your answer" or lowered.startswith(
        "what spiritual gems from this week"
    )


def describe_page(html: str) -> str:
    """Short summary of what a page contained, for error messages."""
    soup = BeautifulSoup(html, "lxml")
    title = clean_text(soup.title.get_text()) if soup.title else "(no title)"
    heads = [
        clean_text(h.get_text())[:40]
        for h in soup.find_all(["h1", "h2", "h3"])
    ][:8]
    body = clean_text(soup.get_text(" "))[:160]
    return f"title={title!r} length={len(html)} headings={heads} text={body!r}"


def find_week_heading(nodes):
    """Index of the week-range heading. Prefers h1, accepts h2/h3."""
    for wanted in (("h1",), ("h2", "h3")):
        for i, node in enumerate(nodes):
            if node.name in wanted and WEEK_RANGE_RE.match(
                clean_text(node.get_text())
            ):
                return i
    return None


def parse_week(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")

    # Walk only semantic tags in document order, so the parser does not
    # depend on wrapper <div> class names.
    nodes = soup.find_all(["h1", "h2", "h3", "p", "li"])

    start_idx = find_week_heading(nodes)
    if start_idx is None:
        raise RuntimeError(
            "Could not locate the week heading. " + describe_page(html)
        )

    result = {
        "week_label": clean_text(nodes[start_idx].get_text()).title(),
        "bible_reading": None,
        "opening_song": None,
        "opening_time": None,
        "sections": [],
    }

    sections_by_name = {}
    current_section = None
    current_point = None
    header_phase = True  # before the first recognised section heading

    for node in nodes[start_idx + 1:]:
        text = clean_text(node.get_text())
        if not text:
            continue

        if node.name == "h1":
            break  # next block

        if node.name == "h2":
            key = text.lower()
            matched = next(
                (label for kw, label in SECTION_KEYWORDS.items() if kw in key),
                None,
            )
            if matched:
                header_phase = False
                # Some pages repeat a section heading before later parts;
                # reuse the existing section instead of duplicating it.
                if matched not in sections_by_name:
                    sections_by_name[matched] = {
                        "section": matched,
                        "song": None,
                        "items": [],
                        "concluding": None,
                    }
                    result["sections"].append(sections_by_name[matched])
                current_section = sections_by_name[matched]
                current_point = None
                continue
            if header_phase and result["bible_reading"] is None:
                result["bible_reading"] = text.title()
            continue

        if node.name == "h3":
            if header_phase:
                song = SONG_INLINE_RE.search(text)
                mins = re.search(r"\((\d+)\s*min", text)
                if song:
                    result["opening_song"] = song.group(1)
                if mins:
                    result["opening_time"] = f"{mins.group(1)} min."
                continue

            if current_section is None:
                continue

            if CONCLUDING_RE.search(text):
                song = SONG_INLINE_RE.search(text)
                mins = re.search(r"\((\d+)\s*min", text)
                current_section["concluding"] = {
                    "time": f"{mins.group(1)} min." if mins else None,
                    "song": song.group(1) if song else None,
                }
                current_point = None
                continue

            point = POINT_RE.match(text)
            if point:
                current_point = {
                    "num": int(point.group(1)),
                    "title": clean_text(point.group(2)),
                    "time": None,
                    "detail": None,
                }
                current_section["items"].append(current_point)
                continue

            song_only = SONG_ONLY_RE.match(text)
            if song_only:
                current_section["song"] = song_only.group(1)
                current_point = None
            continue

        if node.name in ("p", "li"):
            if header_phase or current_point is None or is_boilerplate(text):
                continue

            if current_point["time"] is None:
                m = TIME_RE.match(text)
                if m:
                    current_point["time"] = f"{m.group(1)} min."
                    remainder = clean_text(TIME_RE.sub("", text, count=1))
                    if remainder and not is_boilerplate(remainder):
                        current_point["detail"] = truncate(remainder)
                elif current_point["detail"] is None:
                    current_point["detail"] = truncate(text)
                continue

            if current_point["detail"] is None:
                current_point["detail"] = truncate(text)
            # Otherwise skip the rest of the body to keep the payload short.

    return result


# --------------------------------------------------------------------- main


def get_week_data(target_date: date) -> dict:
    sources = [("wol.jw.org", wol_url(target_date)), ("jw.org", jw_org_url(target_date))]
    errors = []
    for label, url in sources:
        try:
            html = fetch_html(url)
            data = parse_week(html)
            if not data["sections"]:
                raise RuntimeError("page parsed but no sections found")
            data["source_url"] = url
            return data
        except Exception as exc:  # noqa: BLE001
            msg = f"[{label}] {exc}"
            errors.append(msg)
            print(f"Source failed: {msg}", file=sys.stderr)
    raise RuntimeError(" || ".join(errors))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Date (YYYY-MM-DD) inside the target week.")
    parser.add_argument(
        "--tz",
        default="Australia/Sydney",
        help="Timezone used to decide what 'today' is (default: Australia/Sydney).",
    )
    parser.add_argument("--out", default="data/mwb.json")
    args = parser.parse_args()

    if args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        target_date = datetime.now(ZoneInfo(args.tz)).date()

    data = get_week_data(target_date)
    data["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Wrote {args.out} (source: {data['source_url']})")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:2000])


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
