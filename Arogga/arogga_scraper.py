"""
Arogga Category Scraper  —  Selenium version
=============================================
Reads category URLs from links.xlsx, scrapes all product cards from each
page, and saves results to:

    output/<YYYY>/<MM-Month>/arogga_<YYYY-MM-DD>.csv

Requirements:
    pip install selenium openpyxl webdriver-manager

Usage:
    python arogga_scraper.py
    python arogga_scraper.py --links my_links.xlsx
"""

import argparse
import csv
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import openpyxl
except ImportError:
    sys.exit("Missing: pip install openpyxl")

try:
    from selenium import webdriver
    from selenium.common.exceptions import (
        NoSuchElementException,
        StaleElementReferenceException,
        TimeoutException,
    )
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
except ImportError:
    sys.exit("Missing: pip install selenium")


# ── Output path ──────────────────────────────────────────────────────────────
NOW = datetime.now()
YEAR = NOW.strftime("%Y")
MONTH_FOLD = NOW.strftime("%m-%B")  # e.g. "04-April"
DATE_STR = NOW.strftime("%Y-%m-%d")
OUTPUT_DIR = Path("output") / YEAR / MONTH_FOLD
OUTPUT_FILE = OUTPUT_DIR / f"arogga_{DATE_STR}.csv"

# ── Selectors ─────────────────────────────────────────────────────────────────
# Primary: main > section > div:nth-child(3) > a  (from your XPath analysis)
CARD_CSS = "main section > div:nth-child(3) > a"
# Fallback if layout shifts
CARD_CSS_WIDE = "main section a[href*='/product']"

PAGE_LOAD_WAIT = 8  # seconds after page load before scraping
SCROLL_STEP = 800  # px per scroll
SCROLL_PAUSE = 0.8  # seconds between scrolls
REQUEST_DELAY = 3  # seconds between category pages


# ── Helpers ───────────────────────────────────────────────────────────────────
def clean(text: str) -> str:
    return re.sub(
        r"\s+", " ", text.replace("৳", "").replace("Tk", "").replace("\u09f3", "")
    ).strip()


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


def make_driver() -> webdriver.Chrome:
    """Build a stealth Chrome WebDriver."""
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--window-size=1366,900")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--lang=en-US")

    # Selenium 4.6+ auto-downloads the correct ChromeDriver for your Chrome version.
    # No need for webdriver-manager at all.
    driver = webdriver.Chrome(options=opts)

    # Remove webdriver fingerprint via CDP
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": """
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        """
        },
    )
    return driver


def scroll_full_page(driver):
    """Scroll gradually to trigger lazy-loaded product cards."""
    last_height = driver.execute_script("return document.body.scrollHeight")
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(0.5)

    while True:
        current = 0
        while current < last_height:
            current += SCROLL_STEP
            driver.execute_script(f"window.scrollTo(0, {current});")
            time.sleep(SCROLL_PAUSE)

        time.sleep(1.5)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height

    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(1)


def safe_text(el, css: str) -> str:
    """Get text of a child element; return '' if not found."""
    try:
        child = el.find_element(By.CSS_SELECTOR, css)
        return clean(child.text)
    except (NoSuchElementException, StaleElementReferenceException):
        return ""


def get_name_and_volume(card) -> tuple:
    """
    Extract item name and volume from h4.
    h4 contains the full name text; a <span> child holds the volume.
    We strip the span text from h4 to isolate the product name.
    """
    try:
        h4 = card.find_element(By.CSS_SELECTOR, "div:nth-child(2) h4")
        full = clean(h4.text)
    except (NoSuchElementException, StaleElementReferenceException):
        return "", ""

    try:
        span = h4.find_element(By.TAG_NAME, "span")
        volume = clean(span.text)
    except (NoSuchElementException, StaleElementReferenceException):
        volume = ""

    if volume and full.endswith(volume):
        name = full[: -len(volume)].strip()
    elif volume and volume in full:
        name = full.replace(volume, "").strip()
    else:
        name = full

    return name, volume


def scrape_category(driver, url: str) -> list:
    """Visit one category page and return a list of product dicts."""
    print(f"  Loading page...")
    try:
        driver.get(url)
    except Exception as e:
        print(f"  [ERROR] Navigation failed: {e}")
        return []

    # Wait for product cards
    wait = WebDriverWait(driver, 20)
    found_selector = None
    for sel in [CARD_CSS, CARD_CSS_WIDE]:
        try:
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, sel)))
            found_selector = sel
            break
        except TimeoutException:
            continue

    if not found_selector:
        print(f"  [NO PRODUCTS] No cards found. Page title: {driver.title}")
        print(f"  Snippet: {driver.page_source[:400]}")
        return []

    time.sleep(PAGE_LOAD_WAIT)
    print(f"  Scrolling to load all products...")
    scroll_full_page(driver)

    cards = driver.find_elements(By.CSS_SELECTOR, found_selector)
    print(f"  Found {len(cards)} product cards")

    products = []
    for i, card in enumerate(cards):
        try:
            name, volume = get_name_and_volume(card)
            if not name:
                continue

            # Original price is in a <del> tag
            orig_price = safe_text(card, "div:nth-child(2) > div:last-child div del")
            # Discounted price in a <div> sibling of <del>
            disc_price = safe_text(card, "div:nth-child(2) > div:last-child div > div")

            # Broader fallbacks
            if not orig_price:
                orig_price = safe_text(card, "del")
            if not disc_price:
                disc_price = safe_text(
                    card, "[class*='discount'], [class*='price'] div"
                )

            products.append(
                {
                    "item_name": name,
                    "volume": volume,
                    "price": orig_price,
                    "discounted_price": disc_price,
                    "source_url": url,
                }
            )
        except StaleElementReferenceException:
            print(f"  [WARN] Stale element at index {i}, skipping")
        except Exception as exc:
            print(f"  [WARN] Error on card {i}: {exc}")

    return products


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Arogga category scraper (Selenium)")
    parser.add_argument(
        "--links",
        default="links.xlsx",
        help="Excel file with category URLs in column A",
    )
    parser.add_argument(
        "--no-header", action="store_true", help="Excel file has no header row"
    )
    args = parser.parse_args()

    links_path = Path(args.links)
    if not links_path.exists():
        sys.exit(f"❌ Links file not found: {links_path}")

    urls = read_links(links_path, has_header=not args.no_header)
    if not urls:
        sys.exit(
            "❌ No valid URLs found in the Excel file (URLs must be in column A and start with 'http')."
        )

    print(f"✔ Loaded {len(urls)} category URL(s) from {links_path}")
    print(f"✔ Output will be saved to: {OUTPUT_FILE}\n")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    csv_fields = [
        "Serial No",
        "Item Name",
        "Volume/Weight",
        "Price (BDT)",
        "Discounted Price (BDT)",
        "Source URL",
    ]

    serial = 1
    driver = make_driver()

    try:
        with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=csv_fields)
            writer.writeheader()

            for idx, url in enumerate(urls, start=1):
                print(f"\n[{idx}/{len(urls)}] {url}")
                products = scrape_category(driver, url)

                for p in products:
                    writer.writerow(
                        {
                            "Serial No": serial,
                            "Item Name": p["item_name"],
                            "Volume/Weight": p["volume"],
                            "Price (BDT)": p["price"],
                            "Discounted Price (BDT)": p["discounted_price"],
                            "Source URL": p["source_url"],
                        }
                    )
                    serial += 1

                f.flush()
                print(
                    f"  ✔ {len(products)} products scraped  (running total: {serial - 1})"
                )

                if idx < len(urls):
                    print(f"  Waiting {REQUEST_DELAY}s...")
                    time.sleep(REQUEST_DELAY)

    finally:
        driver.quit()

    total = serial - 1
    print(f"\n{'=' * 55}")
    print(f"✅ Scraping complete!")
    print(f"   Total products : {total}")
    print(f"   Saved to       : {OUTPUT_FILE}")
    print(f"{'=' * 55}")


if __name__ == "__main__":
    main()
