# Life and Ministry Meeting Workbook — TRMNL plugin

Displays the current week's Life and Ministry Meeting schedule (from
wol.jw.org) on a TRMNL e-ink display: song numbers, section headers,
part titles, assigned times, and short reference tags — the same
information normally posted on a congregation's information board.

It does **not** pull the full study content of each part (the
discussion questions, video call-outs, etc.) — only the schedule
structure, kept short enough to be glanceable on an e-ink screen.

## How it works

```
wol.jw.org  →  scrape_mwb.py  →  data/mwb.json  →  TRMNL polls the raw
(weekly page)   (GitHub Action,     (committed to      GitHub URL and
                 runs daily)         your repo)         renders it
```

Because TRMNL's Polling strategy needs a stable JSON URL to fetch, and
wol.jw.org itself only returns HTML, a small GitHub Action does the
scraping for you and commits the result to your own repo. TRMNL then
polls that JSON file directly — no server to maintain.

## Setup

### 1. Push this project to a new GitHub repo

```
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

### 2. Run the Action once manually

GitHub repo → **Actions** tab → **Update MWB schedule** → **Run workflow**.
This generates `data/mwb.json` and commits it. After that it runs
automatically every day (schedule is in
`.github/workflows/update-mwb.yml` — edit the cron line if you'd
rather it run at a different time).

If Actions don't have push permission by default on your account:
Settings → Actions → General → Workflow permissions → "Read and write
permissions".

### 3. Grab the raw JSON URL

```
https://raw.githubusercontent.com/YOUR_USERNAME/YOUR_REPO/main/data/mwb.json
```

Open it in a browser to confirm it looks right before wiring it into
TRMNL.

### 4. Create the plugin in TRMNL

Easiest path — import the zip:

1. TRMNL dashboard → **Plugins** → look for an **Import** option (private
   plugin import/export). Upload `mwb-trmnl-plugin.zip` (built alongside
   this README).
2. Open the imported plugin's settings and paste your raw GitHub URL
   from step 3 into **Polling URL** (it currently has a placeholder).
3. Add the plugin to a playlist / your device.

If your account doesn't show a zip-import option, create it manually
instead:

1. Plugins → **Private Plugin** → new plugin.
2. Strategy: **Polling**. Polling URL: your raw GitHub URL from step 3.
   Refresh interval: 1440 (daily) is enough since the source only
   changes weekly.
3. Open the Markup Editor and paste the contents of
   `trmnl-plugin/full.liquid`, `half_horizontal.liquid`,
   `half_vertical.liquid`, and `quadrant.liquid` into their matching
   tabs.
4. Save, then use **Force Refresh** to pull data immediately and
   preview it.

## Testing the scraper without waiting a week

```
pip install -r requirements.txt
python scrape_mwb.py --date 2026-07-09   # any date in the week you want
cat data/mwb.json
```

`tests/test_fixture.html` is a hand-built HTML fixture matching the
real page's heading structure, used to sanity-check the parsing logic
without hitting the network:

```
python3 -c "from scrape_mwb import parse_week; import json; print(json.dumps(parse_week(open('tests/test_fixture.html').read()), indent=2))"
```

## If wol.jw.org changes its markup

The parser doesn't depend on CSS class names — it walks `<h1>`/`<h2>`/
`<h3>`/`<p>`/`<li>` tags in document order and matches on text patterns
(section names, `"N. Title"` headings, `"(N min.)"` durations). This
should survive most styling/redesign changes. If it ever breaks, the
likely fix is adjusting the regexes near the top of `scrape_mwb.py`
(`SECTION_KEYWORDS`, `POINT_RE`, `TIME_RE`, etc.) — run it with
`--out /tmp/test.json` against a fresh date and diff the output against
what's actually on the page for that week.

## Notes

- Timezone: the script resolves "today" in `Australia/Sydney` by
  default (`--tz` to override), so the GitHub Actions runner's UTC
  clock doesn't pick the wrong week near a day boundary.
- The JSON payload only contains schedule metadata (titles, times,
  song numbers, short reference tags truncated to ~100 characters) —
  intentionally not the full paragraph content of each part.
