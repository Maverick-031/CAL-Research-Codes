"""
Chittagong Port Authority — TOS / PCS Dashboard Scraper
=======================================================
Scrapes the public Terminal Operating System (TOS) dashboard and the
Port Community System (PCS) historical reports published by the
Chittagong Port Authority at https://cpatos.gov.bd .

The site exposes TWO different kinds of data, and this scraper handles
both with the right incremental strategy:

  1. SNAPSHOT pages  (real-time "state right now")
     e.g. vesselAtBerth.php, berthOccupance.php, yardOccupance.php
     -> Each run appends ONE timestamped snapshot per page. Over many
        runs this builds a time-series of the live port state.
     -> De-duplicated on the page's own "as of" timestamp, so running
        twice within the same publish window will not create duplicates.

  2. DATE-SERIES reports  (one finalised report per calendar day)
     e.g. /pcs/index.php/report/containerHandlingView/YYYY-MM-DD
     -> Scrapes from a START date forward, one day at a time, and
        REMEMBERS the last date it reached (outputs/.scrape_state.json).
        The next run resumes from the day after — so if you scraped up
        to April, the next run only fetches May and appends it.

PARSING IS STRUCTURE-AGNOSTIC
  Every HTML <table> on a page is read with pandas.read_html (with a
  BeautifulSoup fallback). Whatever columns the port publishes are kept
  verbatim and normalised, so the scraper keeps working even if the port
  re-orders or renames columns. Metadata columns (scraped_at, as_of,
  source_url, report_date) are added by us.

OUTPUT  (all inside ./outputs)
  outputs/snapshots/<page_key>.csv        one row(set) appended per run
  outputs/<series_key>_daily.csv          one row(set) per calendar day
  outputs/.scrape_state.json              resume cursors + known-empty days
  outputs/raw_html/                        (optional, --save-html) raw pages

REQUIREMENTS
  pip install -r requirements.txt
  (requests, pandas, beautifulsoup4, lxml)

USAGE
  python cpa_scraper.py                       # scrape everything, incremental
  python cpa_scraper.py --only snapshots      # only the live snapshot pages
  python cpa_scraper.py --only date-series    # only the daily historical reports
  python cpa_scraper.py --from 2024-01-01     # force date-series start
  python cpa_scraper.py --to   2026-04-30     # stop date-series at this day
  python cpa_scraper.py --refresh-days 7      # also re-pull the last 7 days
  python cpa_scraper.py --reset               # ignore saved cursors, start fresh
  python cpa_scraper.py --save-html           # also keep raw HTML for debugging
"""

import argparse
import hashlib
import io
import json
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    import requests
    import pandas as pd
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("Missing deps. Run:  pip install -r requirements.txt")


# ═══════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════
TOS_BASE = "https://cpatos.gov.bd/tosdashboard/"
PCS_BASE = "https://cpatos.gov.bd/pcs/index.php/"

# ── Real-time snapshot pages ─────────────────────────────────
# key            -> stable file name (do NOT change once data exists)
# url            -> page to fetch
# title          -> human label (documented in README)
SNAPSHOT_PAGES = [
    {"key": "container_vessel_overview", "url": TOS_BASE + "index.php",
     "title": "TOS Dashboard - Container Vessel (overview)"},
    {"key": "vessel_at_berth_container", "url": TOS_BASE + "vesselAtBerth.php",
     "title": "Berth Wise Vessel (Container) Operation"},
    {"key": "vessel_at_berth_breakbulk", "url": TOS_BASE + "vesselAtBerth_BreakBulk.php",
     "title": "Berth Wise Vessel (Break Bulk) Operation"},
    {"key": "vessel_report", "url": TOS_BASE + "vesselReport.php",
     "title": "Vessel Report"},
    {"key": "berth_occupancy", "url": TOS_BASE + "berthOccupance.php",
     "title": "Berth Occupancy"},
    {"key": "yard_occupancy", "url": TOS_BASE + "yardOccupance.php",
     "title": "Container Yard Occupancy"},
    {"key": "day_wise_lying_container", "url": TOS_BASE + "day_wise_lying_container.php",
     "title": "Day Wise Lying Container (yard stock)"},
    {"key": "performance_24h", "url": TOS_BASE + "24HoursperformanceReport.php",
     "title": "24 Hours Performance Report"},
    {"key": "equipment_current_status", "url": TOS_BASE + "mis_equipment_current_status.php",
     "title": "Container Handling Equipment Position (zones AB, C, D & PICT)"},
]

# ── Date-parameterised historical reports ────────────────────
# url_template must contain {date} which is filled with YYYY-MM-DD.
DATE_SERIES_PAGES = [
    {"key": "container_handling",
     "url_template": PCS_BASE + "report/containerHandlingView/{date}",
     "title": "Daily Container Handling / Yardwise Equipment Booking Report",
     "default_start": "2021-01-01"},
]

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_TIMEOUT = 30          # seconds per request
MAX_RETRIES     = 4           # network retries per URL
THROTTLE        = 0.8         # seconds between requests (be polite to a gov server)

OUTPUT_DIR    = Path("outputs")
SNAPSHOT_DIR  = OUTPUT_DIR / "snapshots"
RAW_HTML_DIR  = OUTPUT_DIR / "raw_html"
STATE_FILE    = OUTPUT_DIR / ".scrape_state.json"


# ═══════════════════════════════════════════════════════════
#  HTTP
# ═══════════════════════════════════════════════════════════
def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def fetch(session: requests.Session, url: str) -> str | None:
    """GET a URL with exponential-backoff retries. Returns HTML or None."""
    delay = 2
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = session.get(url, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                return r.text
            # 404 / 500 on a specific date usually = no report that day.
            if r.status_code in (404, 500):
                return None
            print(f"    HTTP {r.status_code} for {url}")
        except requests.RequestException as e:
            print(f"    attempt {attempt}/{MAX_RETRIES} failed: {e}")
        if attempt < MAX_RETRIES:
            time.sleep(delay)
            delay *= 2
    return None


# ═══════════════════════════════════════════════════════════
#  HTML -> DataFrames
# ═══════════════════════════════════════════════════════════
_WS = re.compile(r"\s+")


def _clean_colname(name) -> str:
    """Normalise a column header into a tidy snake-ish label."""
    txt = _WS.sub(" ", str(name)).strip()
    txt = txt.replace("\n", " ")
    return txt if txt and not txt.lower().startswith("unnamed") else ""


def normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    cols, seen = [], {}
    for i, c in enumerate(df.columns):
        name = _clean_colname(c) or f"col_{i+1}"
        # de-duplicate repeated header labels
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        cols.append(name)
    df = df.copy()
    df.columns = cols
    # drop fully-empty rows / columns
    df = df.dropna(how="all").dropna(axis=1, how="all")
    return df


def parse_tables(html: str) -> list[pd.DataFrame]:
    """Return every data table on the page as a normalised DataFrame."""
    tables: list[pd.DataFrame] = []
    try:
        for df in pd.read_html(io.StringIO(html)):
            if df.shape[0] >= 1 and df.shape[1] >= 1:
                tables.append(normalise_columns(df))
    except ValueError:
        pass  # "No tables found" -> fall back below

    if not tables:                                   # BeautifulSoup fallback
        soup = BeautifulSoup(html, "lxml")
        for tbl in soup.find_all("table"):
            rows = []
            for tr in tbl.find_all("tr"):
                cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                if cells:
                    rows.append(cells)
            if len(rows) >= 2:
                width = max(len(r) for r in rows)
                rows = [r + [""] * (width - len(r)) for r in rows]
                df = pd.DataFrame(rows[1:], columns=rows[0])
                tables.append(normalise_columns(df))
    return tables


_AS_OF_RE = re.compile(
    r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?(?:\s*[APMapm]{2})?)?)"
)


def extract_as_of(html: str) -> str:
    """
    Pull the 'as of' date/time the page prints in its heading,
    e.g. 'Berth Wise Vessel(Container) Operation at 25/05/2026 02:30'.
    Falls back to '' if not found.
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    m = re.search(r"\bat\s+" + _AS_OF_RE.pattern, text)
    if m:
        return m.group(1).strip()
    m = _AS_OF_RE.search(text)
    return m.group(1).strip() if m else ""


def page_title(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in ("h1", "h2", "h3", "title"):
        el = soup.find(tag)
        if el and el.get_text(strip=True):
            return _WS.sub(" ", el.get_text(strip=True))
    return ""


# ═══════════════════════════════════════════════════════════
#  CSV merge / append (load existing + add new + de-dupe + write)
# ═══════════════════════════════════════════════════════════
def merge_csv(path: Path, new_rows: pd.DataFrame, dedupe_on: list[str] | None = None):
    """
    Append new_rows to an existing CSV without losing history.
    Column drift is handled by taking the union of columns.
    """
    if new_rows is None or new_rows.empty:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        old = pd.read_csv(path, dtype=str, keep_default_na=False)
        combined = pd.concat([old, new_rows.astype(str)], ignore_index=True)
    else:
        combined = new_rows.astype(str)

    before = len(combined)
    if dedupe_on:
        keys = [c for c in dedupe_on if c in combined.columns]
        if keys:
            combined = combined.drop_duplicates(subset=keys, keep="last")
    else:
        combined = combined.drop_duplicates(keep="last")
    added = len(combined) - (before - len(new_rows))

    combined.to_csv(path, index=False, encoding="utf-8-sig")
    return max(added, 0)


def _content_hash(df: pd.DataFrame) -> str:
    return hashlib.md5(
        pd.util.hash_pandas_object(df.fillna(""), index=False).values.tobytes()
    ).hexdigest()[:12]


def append_snapshot(path: Path, df: pd.DataFrame, key_col: str = "_snapshot_key") -> int:
    """
    Append a whole snapshot (all rows of one table) exactly once.
    If a snapshot with the same key already exists in the file, skip it,
    so re-running before a fresh publish never duplicates rows.
    """
    if df is None or df.empty:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    new_key = str(df[key_col].iloc[0])
    if path.exists():
        old = pd.read_csv(path, dtype=str, keep_default_na=False)
        if key_col in old.columns and new_key in set(old[key_col].astype(str)):
            return 0
        combined = pd.concat([old, df.astype(str)], ignore_index=True)
    else:
        combined = df.astype(str)
    combined.to_csv(path, index=False, encoding="utf-8-sig")
    return len(df)


# ═══════════════════════════════════════════════════════════
#  STATE  (resume cursors)
# ═══════════════════════════════════════════════════════════
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ═══════════════════════════════════════════════════════════
#  SNAPSHOT SCRAPE
# ═══════════════════════════════════════════════════════════
def scrape_snapshots(session, pages, save_html=False) -> dict:
    """Fetch each live page and append a timestamped snapshot."""
    stamp = datetime.now().isoformat(timespec="seconds")
    summary = {}
    for page in pages:
        key, url = page["key"], page["url"]
        print(f"  • {key}  ({url})")
        html = fetch(session, url)
        if not html:
            print("      no response — skipped")
            summary[key] = "no response"
            continue
        if save_html:
            RAW_HTML_DIR.mkdir(parents=True, exist_ok=True)
            (RAW_HTML_DIR / f"{key}.html").write_text(html, encoding="utf-8")

        as_of = extract_as_of(html)
        tables = parse_tables(html)
        if not tables:
            print("      no tables found on page")
            summary[key] = "no tables"
            continue

        total_added = 0
        for idx, df in enumerate(tables, start=1):
            df = df.copy()
            df.insert(0, "scraped_at", stamp)
            df.insert(1, "as_of", as_of)
            df.insert(2, "source_url", url)
            df.insert(3, "table_index", idx)
            # one file per table; most pages have exactly one
            fname = f"{key}.csv" if len(tables) == 1 else f"{key}_t{idx}.csv"
            # one snapshot key for the whole table: the page's publish time if
            # it prints one, else a content hash. Re-running before a fresh
            # publish is then a no-op instead of duplicating the snapshot.
            sig = as_of if as_of else f"hash-{_content_hash(df)}"
            df["_snapshot_key"] = f"{sig}|t{idx}"
            added = append_snapshot(SNAPSHOT_DIR / fname, df)
            total_added += added
        print(f"      {len(tables)} table(s), as_of='{as_of or 'n/a'}', +{total_added} new rows")
        summary[key] = f"+{total_added} rows"
        time.sleep(THROTTLE)
    return summary


# ═══════════════════════════════════════════════════════════
#  DATE-SERIES SCRAPE
# ═══════════════════════════════════════════════════════════
def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def scrape_date_series(session, pages, state, args) -> dict:
    summary = {}
    today = date.today()
    for page in pages:
        key = page["key"]
        st = state.setdefault(key, {})
        empty = set(st.get("empty_dates", []))

        # ── decide the start date ────────────────────────────
        if args.reset:
            start = _parse_date(args.from_date) if args.from_date \
                else _parse_date(page["default_start"])
            empty = set()
        elif args.from_date:
            start = _parse_date(args.from_date)
        elif st.get("cursor"):
            start = _parse_date(st["cursor"]) + timedelta(days=1)
        else:
            start = _parse_date(page["default_start"])

        end = _parse_date(args.to_date) if args.to_date else today

        # optionally re-pull the most recent N days (in case a day was
        # incomplete when first scraped)
        if args.refresh_days and st.get("cursor"):
            refresh_from = today - timedelta(days=args.refresh_days)
            start = min(start, refresh_from)

        print(f"  • {key}: {start.isoformat()} -> {end.isoformat()}")
        if start > end:
            print("      already up to date")
            summary[key] = "up to date"
            continue

        out_path = OUTPUT_DIR / f"{key}_daily.csv"
        total_added, hit, miss, cursor = 0, 0, 0, st.get("cursor")
        for d in daterange(start, end):
            ds = d.isoformat()
            if ds in empty and not args.reset and not args.refresh_days:
                cursor = ds
                continue
            url = page["url_template"].format(date=ds)
            html = fetch(session, url)
            cursor = ds
            if not html:
                empty.add(ds); miss += 1
                continue
            tables = parse_tables(html)
            tables = [t for t in tables if len(t) > 0]
            if not tables:
                empty.add(ds); miss += 1
                continue

            frames = []
            for idx, df in enumerate(tables, start=1):
                df = df.copy()
                df.insert(0, "report_date", ds)
                df.insert(1, "table_index", idx)
                df.insert(2, "source_url", url)
                frames.append(df)
            day_df = pd.concat(frames, ignore_index=True)
            day_df["_row_id"] = [f"{ds}|{i}" for i in range(len(day_df))]
            added = merge_csv(out_path, day_df, dedupe_on=["report_date", "_row_id"])
            total_added += added
            empty.discard(ds)
            hit += 1
            if hit % 25 == 0:
                print(f"      ...{ds}: {hit} days with data, +{total_added} rows so far")
            time.sleep(THROTTLE)

        st["cursor"] = cursor
        st["empty_dates"] = sorted(empty)
        st["last_run"] = datetime.now().isoformat(timespec="seconds")
        print(f"      done: {hit} days with data, {miss} empty, +{total_added} rows")
        summary[key] = f"+{total_added} rows ({hit} days)"
    return summary


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


# ═══════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(description="Chittagong Port Authority dashboard scraper")
    ap.add_argument("--only", choices=["snapshots", "date-series"],
                    help="scrape only one category (default: both)")
    ap.add_argument("--from", dest="from_date", metavar="YYYY-MM-DD",
                    help="force date-series start date")
    ap.add_argument("--to", dest="to_date", metavar="YYYY-MM-DD",
                    help="date-series end date (default: today)")
    ap.add_argument("--refresh-days", type=int, default=0,
                    help="also re-pull the most recent N days of date-series")
    ap.add_argument("--reset", action="store_true",
                    help="ignore saved cursors and scrape from the beginning")
    ap.add_argument("--save-html", action="store_true",
                    help="also save raw HTML of snapshot pages to outputs/raw_html")
    args = ap.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    session = make_session()
    state = load_state()

    print("=" * 64)
    print("  Chittagong Port Authority — TOS/PCS Scraper")
    print(f"  Output : {OUTPUT_DIR.resolve()}")
    print("=" * 64)

    results = {}
    if args.only != "date-series":
        print("\n[1] Real-time snapshot pages")
        results["snapshots"] = scrape_snapshots(session, SNAPSHOT_PAGES, args.save_html)

    if args.only != "snapshots":
        print("\n[2] Date-series historical reports")
        results["date_series"] = scrape_date_series(session, DATE_SERIES_PAGES, state, args)
        save_state(state)

    print("\n" + "=" * 64)
    print("  SUMMARY")
    for category, items in results.items():
        print(f"  {category}:")
        for k, v in items.items():
            print(f"    - {k}: {v}")
    print("=" * 64)
    print("Done. Run cpa_analysis.py to build charts and the insights report.")


if __name__ == "__main__":
    main()
