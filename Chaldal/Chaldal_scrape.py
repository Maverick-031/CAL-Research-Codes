"""
Chaldal Product Scraper
=======================
Reads a list of Chaldal category URLs from an Excel file,
scrapes all product cards on each page, and saves results to
a date-stamped CSV.

Requirements:
    pip install selenium openpyxl pandas beautifulsoup4

Chrome/Chromium + chromedriver must be installed:
    sudo apt-get install -y chromium-browser chromium-chromedriver   # Debian/Ubuntu
    brew install --cask chromedriver                                   # macOS

Usage (simplest — just run it):
    python chaldal_scraper.py

    By default it looks for 'links.xlsx' in the same folder and reads the first column.

Advanced usage:
    python chaldal_scraper.py --input my_links.xlsx --url-column "Category URL"
    python chaldal_scraper.py --headless false
"""

import argparse
import time
import os
from datetime import date

import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException


# ── helpers ────────────────────────────────────────────────────────────────────

def build_driver(headless: bool = True) -> webdriver.Chrome:
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    
    # Required for GitHub Actions / Linux environments
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    
    return webdriver.Chrome(options=opts)


def scroll_to_bottom(driver, pause: float = 1.5, max_scrolls: int = 60):
    """Scroll incrementally so lazy-loaded products render."""
    last_height = driver.execute_script("return document.body.scrollHeight")
    for _ in range(max_scrolls):
        driver.execute_script("window.scrollBy(0, 800);")
        time.sleep(pause)
        new_height = driver.execute_script("return document.body.scrollHeight")
        # Also check if product count has stabilised for 2 consecutive rounds
        if new_height == last_height:
            break
        last_height = new_height


def parse_html_snapshot(html: str, page_url: str) -> list[dict]:
    """
    Parse product cards from a static HTML snapshot using BeautifulSoup.
    No live Selenium element references — immune to StaleElementReferenceException.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    products = []

    for wrapper in soup.select("div.textWrapper"):
        # ── name ─────────────────────────────────────────────────────────────
        name_tag = wrapper.select_one("p.nameTextWithEllipsis")
        name = name_tag.get_text(strip=True) if name_tag else ""

        # ── quantity ──────────────────────────────────────────────────────────
        sub = wrapper.select_one("div.subText > span")
        quantity = sub.get_text(strip=True) if sub else ""

        # ── price ─────────────────────────────────────────────────────────────
        price = ""

        # Case 1: discounted price block exists
        # Structure: div.productV2discountedPrice
        #   > [div.currency][span = DISCOUNTED][div.price > [div.currency][span = original]]
        disc_block = wrapper.select_one("div.productV2discountedPrice")
        if disc_block:
            # Direct <span> children only (skip spans inside nested div.price)
            for child in disc_block.children:
                if hasattr(child, "name") and child.name == "span":
                    price = child.get_text(strip=True)
                    break

        # Case 2: plain price — div.price > span (no discount wrapper)
        if not price:
            plain_block = wrapper.select_one("div.price")
            if plain_block:
                span = plain_block.find("span")
                if span:
                    price = span.get_text(strip=True)
                else:
                    price = plain_block.get_text(strip=True).replace("৳", "").strip()

        if name:
            products.append({
                "Item Name": name,
                "Quantity/Volume": quantity,
                "Price (BDT)": price,
                "Link": page_url,
            })

    return products


def parse_products(driver, page_url: str, max_retries: int = 3) -> list[dict]:
    """
    Load a page, scroll to trigger lazy loading, then take a static HTML
    snapshot and parse it — retrying up to max_retries times on any error.
    """
    for attempt in range(1, max_retries + 1):
        try:
            # Wait until at least one product name is present
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "p.nameTextWithEllipsis"))
            )

            # Scroll to trigger lazy loading
            scroll_to_bottom(driver)

            # ── snapshot the DOM as static HTML ───────────────────────────────
            # All parsing happens on this string — no more live element refs
            html_snapshot = driver.execute_script("return document.documentElement.outerHTML")

            products = parse_html_snapshot(html_snapshot, page_url)
            print(f"  Found {len(products)} product cards")
            return products

        except TimeoutException:
            print(f"  [WARN] No products found on {page_url}")
            return []
        except Exception as e:
            if attempt < max_retries:
                wait = attempt * 3
                print(f"  [RETRY {attempt}/{max_retries}] {type(e).__name__} — retrying in {wait}s...")
                time.sleep(wait)
                try:
                    driver.get(page_url)
                    time.sleep(3)
                except Exception:
                    pass
            else:
                print(f"  [FAILED after {max_retries} attempts] {type(e).__name__}: {e}")
                return []

    return []


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Scrape Chaldal category pages")
    parser.add_argument(
        "--input", default=None,
        help="Excel file with URLs (default: 'links.xlsx' in the same folder)"
    )
    parser.add_argument(
        "--url-column", default=None,
        help="Column name containing URLs (default: first column in the file)"
    )
    parser.add_argument(
        "--headless", default="true", choices=["true", "false"],
        help="Run browser headless (default: true)"
    )
    parser.add_argument("--pause", type=float, default=1.5, help="Scroll pause in seconds")
    args = parser.parse_args()

    headless = args.headless.lower() == "true"

    # ── resolve input file ────────────────────────────────────────────────────
    input_file = args.input or "links.xlsx"
    if not os.path.exists(input_file):
        raise FileNotFoundError(
            f"Could not find '{input_file}'. "
            "Place your Excel file in the same folder as this script "
            "and name it 'links.xlsx', or pass --input <filename>."
        )

    # ── read URLs from Excel ──────────────────────────────────────────────────
    df_links = pd.read_excel(input_file)

    # Resolve column — use first column if not specified
    if args.url_column:
        col = args.url_column
        if col not in df_links.columns:
            raise ValueError(
                f"Column '{col}' not found. "
                f"Available columns: {list(df_links.columns)}"
            )
    else:
        col = df_links.columns[0]
        print(f"No --url-column specified. Using first column: '{col}'")

    urls = df_links[col].dropna().str.strip().tolist()
    print(f"Loaded {len(urls)} URLs from '{input_file}' (column: '{col}')")

    # ── output file ───────────────────────────────────────────────────────────
    today = date.today().strftime("%Y-%m-%d")
    output_file = f"chaldal_products_{today}.csv"

    all_products = []
    driver = build_driver(headless=headless)

    try:
        for idx, url in enumerate(urls, 1):
            print(f"\n[{idx}/{len(urls)}] Scraping: {url}")
            try:
                driver.get(url)
                time.sleep(3)          # let React hydrate
                products = parse_products(driver, url)
                all_products.extend(products)
                print(f"  → {len(products)} products scraped")
            except Exception as e:
                print(f"  [ERROR] {e}")
    finally:
        driver.quit()

    # ── write CSV ─────────────────────────────────────────────────────────────
    if all_products:
        df_out = pd.DataFrame(all_products, columns=["Item Name", "Quantity/Volume", "Price (BDT)", "Link"])
        df_out.to_csv(output_file, index=False, encoding="utf-8-sig")
        print(f"\n✅ Done! {len(all_products)} products saved to '{output_file}'")
    else:
        print("\n⚠️  No products scraped. Check URLs or increase --pause value.")


if __name__ == "__main__":
    main()
