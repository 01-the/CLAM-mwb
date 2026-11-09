#!/usr/bin/env python3
"""
scrape_daily.py

Python port of the "jw-daily-text" Cloudflare Worker. Fetches today's daily
text from wol.jw.org's JSON feed and writes JSON in exactly the same shape
the Worker returns, so the TRMNL template needs no changes.

Usage:
    python scrape_daily.py                       # today (Sydney date)
    python scrape_daily.py --date 2026-11-09     # force a date
    python scrape_daily.py --out data/daily.json

Behaviour differences from the Worker:
  * If the fetch fails, the previous JSON file is left untouched.
  * The file is only rewritten when the content changes.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

WOL_LANG_PATH = "lp-e"  # English
WOL_RS = "r1"
TIMEZONE = "Australia/Sydney"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def strip_tags(s: str) -> str:
    # Same replacement chain, in the same order, as the Worker.
    s = re.sub(r"<[^>]+>", "", s)
    for old, new in (
        ("&nbsp;", " "),
        ("&amp;", "&"),
        ("&quot;", '"'),
        ("&#39;", "\u2019"),
        ("&mdash;", "\u2014"),
        ("&#8212;", "\u2014"),
        ("&#8203;", ""),
        ("&para;", "\u00b6"),
        ("\u200b", ""),
    ):
        s = s.replace(old, new)
    return re.sub(r"\s+", " ", s).strip()


def fetch_feed(year: int, month: int, day: int, attempts: int = 3) -> dict:
    url = f"https://wol.jw.org/wol/dt/{WOL_RS}/{WOL_LANG_PATH}/{year}/{month}/{day}"
    last_exc = None
    for i in range(attempts):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=45)
            resp.raise_for_status()
            try:
                return resp.json()
            except ValueError:
                if "not a robot" in resp.text.lower():
                    raise RuntimeError(f"Robot-check page returned for {url}")
                raise RuntimeError(f"Upstream did not return valid JSON ({url})")
        except (requests.RequestException, RuntimeError) as exc:
            last_exc = exc
            if i < attempts - 1:
                wait = 5 * (i + 1)
                print(f"Fetch failed ({exc}); retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
    raise last_exc


def build_payload(data: dict, year: int, month: int, day: int) -> dict:
    items = data.get("items") if isinstance(data, dict) else None
    item = items[0] if items else None
    if not item or not item.get("content"):
        raise RuntimeError("Unexpected upstream response shape")

    html = item["content"]

    # Heading looks like: <h2 ...>Wednesday, July 8</h2>
    hm = re.search(r"<h2[^>]*>(.*?)</h2>", html, re.S)
    heading = strip_tags(hm.group(1)) if hm else f"{year}-{month}-{day}"
    comma = heading.find(",")
    weekday = heading if comma == -1 else heading[:comma].strip()
    month_day = "" if comma == -1 else heading[comma + 1:].strip()

    # Every <p ...>...</p> block. [0] = scripture, [1] = reflection.
    paragraphs = re.findall(r"<p[^>]*>([\s\S]*?)</p>", html)

    scripture = ""
    reference = ""
    if len(paragraphs) > 0 and paragraphs[0]:
        text = strip_tags(paragraphs[0])
        dash = text.rfind("\u2014")
        if dash != -1:
            scripture = text[:dash].strip()
            reference = re.sub(r"\.$", "", text[dash + 1:]).strip()
        else:
            scripture = text

    body = ""
    source = ""
    if len(paragraphs) > 1 and paragraphs[1]:
        body_html = paragraphs[1]
        sm = re.search(r"<a[^>]*>([^<]+)</a>\s*$", body_html)
        if sm:
            source = strip_tags(sm.group(1)).strip()
            body_html = body_html[:sm.start()]
        body = strip_tags(body_html)

    return {
        "weekday": weekday,
        "month_day": month_day,
        "year": year,
        "date": heading,
        "scripture": scripture,
        "reference": reference,
        "body": body,
        "source": source,
        "title": strip_tags(item["title"]) if item.get("title") else "",
    }


def same_content(path: str, new: dict) -> bool:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f) == new
    except (OSError, ValueError):
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYY-MM-DD (default: today in Sydney)")
    parser.add_argument("--tz", default=TIMEZONE)
    parser.add_argument("--out", default="data/daily.json")
    args = parser.parse_args()

    if args.date:
        d = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        d = datetime.now(ZoneInfo(args.tz)).date()

    payload = build_payload(fetch_feed(d.year, d.month, d.day), d.year, d.month, d.day)

    if same_content(args.out, payload):
        print(f"No change in {args.out}")
        return

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"Wrote {args.out}")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
