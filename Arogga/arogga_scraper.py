"""
Arogga Category Scraper  —  Fast Parallel Version
==================================================
FEATURES
  • Parallel scraping  : N Chrome workers run simultaneously (default 3)
  • Resume support     : scrape_log.json tracks every URL — restart anytime
                         and already-completed URLs are skipped automatically
  • Instant CSV save   : each URL's products are written & flushed immediately
  • Smart waits        : dynamic element waits instead of fixed sleeps
  • Progress display   : live per-worker status + running totals
  • Retry on failure   : failed URLs are retried once before marking as failed

OUTPUT
  output/<YYYY>/<MM-Month>/arogga_<YYYY-MM-DD>.csv

LOG FILE
  scrape_log.json   (sits next to the script)
  Contains every URL with status: pending | done | failed
  Delete this file to start a completely fresh run.

REQUIREMENTS
  pip install selenium openpyxl

USAGE
  python arogga_scraper.py                        # default: 3 workers
  python arogga_scraper.py --workers 5            # more parallel workers
  python arogga_scraper.py --links my_links.xlsx  # custom links file
  python arogga_scraper.py --reset                # ignore log, start fresh
"""

import argparse
import csv
import json
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from queue import Queue, Empty

try:
    import openpyxl
except ImportError:
    sys.exit("❌ Missing: pip install openpyxl")

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import (
        TimeoutException, NoSuchElementException, StaleElementReferenceException,
        WebDriverException,
    )
except ImportError:
    sys.exit("❌ Missing: pip install selenium")


# ═══════════════════════════════════════════════════════════
#  CONFIGURATION  — tweak these to tune performance
# ═══════════════════════════════════════════════════════════
DEFAULT_WORKERS    = 3      # parallel Chrome instances (raise to 4-5 if PC allows)
DEFAULT_LINKS_FILE = "links.xlsx"
LOG_FILE           = Path("scrape_log.json")

# Timing (seconds)
PAGE_WAIT_TIMEOUT  = 20     # max wait for product cards to appear
SCROLL_STEP        = 1200   # px per scroll (larger = fewer steps = faster)
SCROLL_PAUSE       = 0.5    # between each scroll step
SCROLL_SETTLE      = 1.2    # after scrolling stops, wait for new cards to render
BETWEEN_RETRIES    = 3      # pause before retrying a failed URL
MAX_RETRIES        = 1      # how many times to retry a failed URL

# Selectors
CARD_CSS       = "main section > div:nth-child(3) > a"
CARD_FALLBACK  = "main section a[href*='/product']"

# ── Output paths ─────────────────────────────────────────
NOW          = datetime.now()
OUTPUT_DIR   = Path("output") / NOW.strftime("%Y") / NOW.strftime("%m-%B")
OUTPUT_FILE  = OUTPUT_DIR / f"arogga_{NOW.strftime('%Y-%m-%d')}.csv"
CSV_LOCK     = threading.Lock()   # one thread writes to CSV at a time
LOG_LOCK     = threading.Lock()   # one thread updates the log at a time
SERIAL_LOCK  = threading.Lock()
_serial_counter = [1]             # mutable container so threads can share it


# ═══════════════════════════════════════════════════════════
#  LOG  (scrape_log.json)
# ═══════════════════════════════════════════════════════════
def log_load() -> dict:
    if LOG_FILE.exists():
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def log_save(log: dict):
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


def log_set(log: dict, url: str, status: str, count: int = 0):
    """Thread-safe update of one URL's status in the log."""
    with LOG_LOCK:
        log[url] = {"status": status, "count": count, "ts": datetime.now().isoformat()}
        log_save(log)


# ═══════════════════════════════════════════════════════════
#  EXCEL READER
# ═══════════════════════════════════════════════════════════
def read_links(path: Path, has_header: bool = True) -> list:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    urls = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0 and has_header:
            continue
        val = row[0] if row else None
        if val and str(val).strip().startswith("http"):
            urls.append(str(val).strip())
    wb.close()
    return urls


# ═══════════════════════════════════════════════════════════
#  SELENIUM DRIVER
# ═══════════════════════════════════════════════════════════
def make_driver() -> webdriver.Chrome:
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--window-size=1366,900")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    # Block images & fonts — pages load faster, product text still scrapes fine
    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.managed_default_content_settings.fonts":  2,
    }
    opts.add_experimental_option("prefs", prefs)
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--lang=en-US")

            # ←←← ADD THIS LINE (required on GitHub Linux runner)
    opts.binary_location = "/usr/bin/chromium-browser"

    driver = webdriver.Chrome(options=opts)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": """
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US','en'] });
        """},
    )
    return driver


# ═══════════════════════════════════════════════════════════
#  SCRAPING HELPERS
# ═══════════════════════════════════════════════════════════
def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("৳","").replace("Tk","").replace("\u09f3","")).strip()


def safe_text(el, css: str) -> str:
    try:
        return clean(el.find_element(By.CSS_SELECTOR, css).text)
    except (NoSuchElementException, StaleElementReferenceException):
        return ""


def get_name_and_volume(card) -> tuple:
    try:
        h4   = card.find_element(By.CSS_SELECTOR, "div:nth-child(2) h4")
        full = clean(h4.text)
    except (NoSuchElementException, StaleElementReferenceException):
        return "", ""
    try:
        volume = clean(h4.find_element(By.TAG_NAME, "span").text)
    except (NoSuchElementException, StaleElementReferenceException):
        volume = ""

    if volume and full.endswith(volume):
        name = full[:-len(volume)].strip()
    elif volume and volume in full:
        name = full.replace(volume, "").strip()
    else:
        name = full
    return name, volume


def scroll_and_load(driver):
    """
    Scroll the page in chunks until height stops growing.
    Blocks images so this is much faster than before.
    """
    last_h = driver.execute_script("return document.body.scrollHeight")
    driver.execute_script("window.scrollTo(0,0)")
    time.sleep(0.3)

    while True:
        pos = 0
        while pos < last_h:
            pos += SCROLL_STEP
            driver.execute_script(f"window.scrollTo(0,{pos})")
            time.sleep(SCROLL_PAUSE)
        time.sleep(SCROLL_SETTLE)
        new_h = driver.execute_script("return document.body.scrollHeight")
        if new_h == last_h:
            break
        last_h = new_h

    driver.execute_script("window.scrollTo(0,0)")


def scrape_one_url(driver, url: str) -> list:
    """Load a single category URL and return list of product dicts."""
    try:
        driver.get(url)
    except WebDriverException as e:
        raise RuntimeError(f"Navigation error: {e}")

    # Dynamic wait — stop as soon as cards appear (no fixed 8s sleep)
    wait = WebDriverWait(driver, PAGE_WAIT_TIMEOUT)
    found_sel = None
    for sel in [CARD_CSS, CARD_FALLBACK]:
        try:
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, sel)))
            found_sel = sel
            break
        except TimeoutException:
            continue

    if not found_sel:
        snippet = driver.page_source[:300].replace("\n", " ")
        raise RuntimeError(f"No product cards found. Snippet: {snippet}")

    scroll_and_load(driver)

    cards = driver.find_elements(By.CSS_SELECTOR, found_sel)
    products = []
    for i, card in enumerate(cards):
        try:
            name, volume = get_name_and_volume(card)
            if not name:
                continue
            orig  = safe_text(card, "div:nth-child(2) > div:last-child div del") or safe_text(card, "del")
            disc  = safe_text(card, "div:nth-child(2) > div:last-child div > div") or safe_text(card, "[class*='discount'] div")
            products.append({"item_name": name, "volume": volume,
                             "price": orig, "discounted_price": disc, "source_url": url})
        except StaleElementReferenceException:
            pass
        except Exception:
            pass
    return products


# ═══════════════════════════════════════════════════════════
#  CSV WRITER  (thread-safe append)
# ═══════════════════════════════════════════════════════════
CSV_FIELDS = ["Serial No","Item Name","Volume/Weight","Price (BDT)","Discounted Price (BDT)","Source URL"]

def append_to_csv(products: list):
    """Append a batch of products to the shared CSV (thread-safe)."""
    if not products:
        return
    with CSV_LOCK:
        with open(OUTPUT_FILE, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            for p in products:
                with SERIAL_LOCK:
                    serial = _serial_counter[0]
                    _serial_counter[0] += 1
                writer.writerow({
                    "Serial No":              serial,
                    "Item Name":              p["item_name"],
                    "Volume/Weight":          p["volume"],
                    "Price (BDT)":            p["price"],
                    "Discounted Price (BDT)": p["discounted_price"],
                    "Source URL":             p["source_url"],
                })


# ═══════════════════════════════════════════════════════════
#  WORKER  (runs in its own thread, owns its own Chrome)
# ═══════════════════════════════════════════════════════════
def worker(worker_id: int, queue: Queue, log: dict, stats: dict, stats_lock: threading.Lock):
    tag = f"[Worker-{worker_id}]"
    driver = None
    try:
        driver = make_driver()
        print(f"{tag} Chrome started ✔")

        while True:
            try:
                url = queue.get(timeout=5)
            except Empty:
                break

            print(f"{tag} → {url}")
            success = False

            for attempt in range(1, MAX_RETRIES + 2):  # +2 = initial + retries
                try:
                    products = scrape_one_url(driver, url)
                    append_to_csv(products)
                    log_set(log, url, "done", len(products))

                    with stats_lock:
                        stats["done"]    += 1
                        stats["products"] += len(products)
                    done  = stats["done"]
                    total = stats["total"]
                    pct   = done / total * 100 if total else 0
                    print(f"{tag} ✔ {len(products)} products  |  {done}/{total} ({pct:.1f}%) complete")
                    success = True
                    break

                except Exception as e:
                    if attempt <= MAX_RETRIES:
                        print(f"{tag} ⚠ Attempt {attempt} failed: {e} — retrying in {BETWEEN_RETRIES}s")
                        time.sleep(BETWEEN_RETRIES)
                        # Restart driver if it crashed
                        try:
                            driver.current_url
                        except WebDriverException:
                            print(f"{tag} Driver crashed, restarting...")
                            try: driver.quit()
                            except: pass
                            driver = make_driver()
                    else:
                        print(f"{tag} ✘ FAILED after {attempt} attempts: {e}")
                        log_set(log, url, "failed", 0)
                        with stats_lock:
                            stats["failed"] += 1

            queue.task_done()

    finally:
        if driver:
            try: driver.quit()
            except: pass
        print(f"{tag} Chrome closed.")


# ═══════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="Arogga parallel scraper with resume")
    parser.add_argument("--links",     default=DEFAULT_LINKS_FILE, help="Excel file with URLs in column A")
    parser.add_argument("--workers",   type=int, default=DEFAULT_WORKERS, help="Parallel Chrome instances (default 3)")
    parser.add_argument("--no-header", action="store_true", help="Excel has no header row")
    parser.add_argument("--reset",     action="store_true", help="Ignore existing log and scrape everything fresh")
    args = parser.parse_args()

    # ── Load links ──────────────────────────────────────────
    links_path = Path(args.links)
    if not links_path.exists():
        sys.exit(f"❌ Links file not found: {links_path}")
    all_urls = read_links(links_path, has_header=not args.no_header)
    if not all_urls:
        sys.exit("❌ No valid URLs found in column A of the Excel file.")

    # ── Load / reset log ────────────────────────────────────
    log = {} if args.reset else log_load()

    # Seed log with any new URLs not seen before
    for url in all_urls:
        if url not in log:
            log[url] = {"status": "pending", "count": 0, "ts": ""}
    log_save(log)

    # Only queue URLs that are not already done
    pending = [u for u in all_urls if log.get(u, {}).get("status") != "done"]
    already_done = len(all_urls) - len(pending)

    print(f"\n{'═'*60}")
    print(f"  Arogga Scraper  —  Parallel Mode ({args.workers} workers)")
    print(f"{'═'*60}")
    print(f"  Total URLs      : {len(all_urls)}")
    print(f"  Already done    : {already_done}  (skipping)")
    print(f"  To scrape now   : {len(pending)}")
    print(f"  Output file     : {OUTPUT_FILE}")
    print(f"  Log file        : {LOG_FILE}")
    print(f"{'═'*60}\n")

    if not pending:
        print("✅ Nothing to do — all URLs already scraped.")
        print("   Run with --reset to force a fresh scrape.")
        return

    # ── Prepare output file ─────────────────────────────────
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Write header only if file doesn't exist yet (resume-safe)
    if not OUTPUT_FILE.exists():
        with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig") as f:
            csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()
    else:
        # Count existing rows to continue serial numbering correctly
        with open(OUTPUT_FILE, "r", encoding="utf-8-sig") as f:
            existing = sum(1 for _ in f) - 1  # subtract header
        _serial_counter[0] = max(1, existing + 1)
        print(f"  Resuming from serial #{_serial_counter[0]}  ({existing} products already in CSV)")

    # ── Fill queue ───────────────────────────────────────────
    q = Queue()
    for url in pending:
        q.put(url)

    stats      = {"done": 0, "failed": 0, "products": 0, "total": len(pending)}
    stats_lock = threading.Lock()

    # ── Launch workers ───────────────────────────────────────
    n_workers = min(args.workers, len(pending))
    threads   = []
    t_start   = time.time()

    for i in range(1, n_workers + 1):
        t = threading.Thread(
            target=worker,
            args=(i, q, log, stats, stats_lock),
            daemon=True,
        )
        t.start()
        threads.append(t)
        time.sleep(1.5)   # stagger starts so Chrome instances don't all hit the site simultaneously

    for t in threads:
        t.join()

    elapsed = time.time() - t_start
    total_products = stats["products"]
    done_count     = stats["done"]
    fail_count     = stats["failed"]

    print(f"\n{'═'*60}")
    print(f"✅ Scraping complete!")
    print(f"   URLs scraped    : {done_count}  ({fail_count} failed)")
    print(f"   Total products  : {total_products}")
    print(f"   Time taken      : {elapsed/60:.1f} minutes")
    if done_count:
        print(f"   Avg per URL     : {elapsed/done_count:.1f}s")
    print(f"   Output file     : {OUTPUT_FILE}")
    if fail_count:
        failed_urls = [u for u, v in log.items() if v["status"] == "failed"]
        print(f"\n   ⚠ Failed URLs ({fail_count}):")
        for u in failed_urls:
            print(f"     {u}")
        print(f"\n   Re-run the script to retry failed URLs automatically.")
    print(f"{'═'*60}")


if __name__ == "__main__":
    main()
