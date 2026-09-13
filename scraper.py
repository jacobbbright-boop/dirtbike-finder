"""
Dirt Bike Deal Finder
---------------------
Scrapes KSL Classifieds + Craigslist for used dirt bikes near a given location,
scores listings against a rolling price baseline, and writes a static
dashboard.html + listings.json.

Run manually:  python scraper.py
Run on a schedule: see .github/workflows/run.yml
"""

import json
import re
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# CONFIG -- edit these for your search
# ---------------------------------------------------------------------------

# Craigslist region subdomain for your area. Find yours at https://craigslist.org
# (Saratoga Springs / Lehi, UT -> Salt Lake City craigslist covers Utah County)
CRAIGSLIST_REGION = "saltlakecity"

# Search keywords to run against each site. One request per keyword.
SEARCH_TERMS = ["dirt bike", "motocross", "enduro", "dual sport"]

# Skip listings outside this price range (set MAX_PRICE=None for no cap)
MIN_PRICE = 300
MAX_PRICE = 12000

# A listing is flagged as a "deal" if it's at least this % below the median
# price of similar bikes (grouped by displacement bucket, see group_key()).
DEAL_THRESHOLD_PCT = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
HISTORY_FILE = DATA_DIR / "listings_history.json"
OUTPUT_JSON = Path(__file__).parent / "listings.json"
OUTPUT_HTML = Path(__file__).parent / "dashboard.html"


# ---------------------------------------------------------------------------
# SCRAPERS
# ---------------------------------------------------------------------------

def fetch_craigslist(term):
    """Scrape a Craigslist motorcycles/scooters (mca) search for a term."""
    url = (
        f"https://{CRAIGSLIST_REGION}.craigslist.org/search/mca"
        f"?query={quote_plus(term)}&sort=date"
    )
    listings = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"[craigslist] request failed for '{term}': {e}")
        return listings

    soup = BeautifulSoup(r.text, "html.parser")
    # Craigslist's markup changes periodically -- these are the current
    # (2025-2026 era) result item classes. If this starts returning 0
    # results, open the search URL above in a browser, inspect a result
    # card, and update the selectors below.
    cards = soup.select("li.cl-search-result, div.cl-search-result")
    for card in cards:
        title_el = card.select_one("a.cl-app-anchor, a.result-title, .titlestring")
        price_el = card.select_one(".priceinfo, .result-price")
        link_el = card.select_one("a.cl-app-anchor, a.result-title")
        if not title_el or not link_el:
            continue
        title = title_el.get_text(strip=True)
        price = _parse_price(price_el.get_text(strip=True)) if price_el else None
        href = link_el.get("href")
        listings.append({
            "source": "craigslist",
            "title": title,
            "price": price,
            "url": href,
            "location": CRAIGSLIST_REGION,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        })
    return listings


def fetch_ksl(term):
    """
    Best-effort KSL Classifieds scraper.

    NOTE: KSL's classifieds site (classifieds.ksl.com) is a JavaScript
    single-page app and actively blocks plain HTTP scraping in many cases
    (you'll see a "This request has been blocked" page). A pure requests+
    BeautifulSoup approach can be unreliable here.

    If this function returns 0 results consistently, the practical fixes are:
      1. Swap this out for a headless-browser fetch (Playwright/Selenium),
         which renders the JS and is much less likely to be blocked.
      2. Use a scraping service (e.g. Apify's KSL scraper) and pull results
         via their API instead of scraping directly.
    Both are drop-in replacements -- just make this function return the same
    list-of-dicts shape.
    """
    url = (
        "https://classifieds.ksl.com/search/keyword/"
        f"{quote_plus(term)}"
    )
    listings = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"[ksl] request failed for '{term}': {e}")
        return listings

    if "This request has been blocked" in r.text:
        print(f"[ksl] blocked while searching '{term}' -- see docstring for fixes")
        return listings

    soup = BeautifulSoup(r.text, "html.parser")
    cards = soup.select("div.adBox, div[class*='listingCard']")
    for card in cards:
        title_el = card.select_one("a.listlink, a[class*='title']")
        price_el = card.select_one(".priceBox, [class*='price']")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        price = _parse_price(price_el.get_text(strip=True)) if price_el else None
        href = title_el.get("href")
        if href and href.startswith("/"):
            href = "https://classifieds.ksl.com" + href
        listings.append({
            "source": "ksl",
            "title": title,
            "price": price,
            "url": href,
            "location": "UT",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        })
    return listings


def _parse_price(text):
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None


# ---------------------------------------------------------------------------
# SCORING
# ---------------------------------------------------------------------------

DISPLACEMENT_RE = re.compile(r"(\d{2,3})\s?cc\b", re.IGNORECASE)
MODEL_RE = re.compile(
    r"\b(yz\d{2,3}f?|crf\d{2,3}[rlx]?|kx\d{2,3}f?|rm-?z?\d{2,3}|"
    r"ktm\s?\d{2,3}|husqvarna\s?\w{2}\d{2,3}|sxf?\s?\d{2,3}|exc\s?\d{2,3})\b",
    re.IGNORECASE,
)


def group_key(title):
    """Bucket a listing so we compare like-for-like bikes."""
    model = MODEL_RE.search(title)
    if model:
        return re.sub(r"\s+", "", model.group(1).lower())
    disp = DISPLACEMENT_RE.search(title)
    if disp:
        cc = int(disp.group(1))
        bucket = (cc // 50) * 50  # bucket into 50cc bands
        return f"{bucket}-{bucket+49}cc"
    return "unclassified"


def score_listings(listings):
    """Attach group + deal-score info to each listing, using price medians
    within this batch (a bigger persistent history = better medians over time)."""
    by_group = {}
    for item in listings:
        if item["price"] is None:
            continue
        key = group_key(item["title"])
        item["group"] = key
        by_group.setdefault(key, []).append(item["price"])

    medians = {k: statistics.median(v) for k, v in by_group.items() if len(v) >= 2}

    for item in listings:
        median = medians.get(item.get("group"))
        if median and item["price"]:
            pct_below = round((median - item["price"]) / median * 100, 1)
            item["median_group_price"] = median
            item["pct_below_median"] = pct_below
            item["is_deal"] = pct_below >= DEAL_THRESHOLD_PCT
        else:
            item["median_group_price"] = None
            item["pct_below_median"] = None
            item["is_deal"] = False
    return listings


# ---------------------------------------------------------------------------
# PERSISTENCE
# ---------------------------------------------------------------------------

def load_history():
    if HISTORY_FILE.exists():
        return json.loads(HISTORY_FILE.read_text())
    return {}


def save_history(history):
    HISTORY_FILE.write_text(json.dumps(history, indent=2))


def merge_into_history(history, new_listings):
    """Key by URL so re-scraping the same ad doesn't duplicate it."""
    for item in new_listings:
        if item.get("url"):
            history[item["url"]] = item
    return history


# ---------------------------------------------------------------------------
# DASHBOARD
# ---------------------------------------------------------------------------

def render_dashboard(all_listings):
    rows_html = []
    deals = sorted(
        [l for l in all_listings if l.get("is_deal")],
        key=lambda x: x["pct_below_median"],
        reverse=True,
    )
    others = sorted(
        [l for l in all_listings if not l.get("is_deal")],
        key=lambda x: x.get("fetched_at", ""),
        reverse=True,
    )

    def row(item, highlight=False):
        price = f"${item['price']:,}" if item.get("price") else "?"
        median = f"${int(item['median_group_price']):,}" if item.get("median_group_price") else "—"
        pct = f"{item['pct_below_median']}% below group median" if item.get("pct_below_median") is not None else ""
        cls = "deal" if highlight else ""
        return f"""
        <tr class="{cls}">
          <td><a href="{item.get('url','#')}" target="_blank">{item['title']}</a></td>
          <td>{price}</td>
          <td>{median}</td>
          <td>{pct}</td>
          <td>{item.get('source')}</td>
          <td>{item.get('group','')}</td>
        </tr>"""

    for item in deals:
        rows_html.append(row(item, highlight=True))
    for item in others:
        rows_html.append(row(item, highlight=False))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Dirt Bike Deal Finder</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }}
  h1 {{ font-size: 1.4rem; margin-bottom:4px; }}
  .meta {{ color:#9aa0a6; margin-bottom:20px; font-size:0.85rem; }}
  table {{ width:100%; border-collapse: collapse; }}
  th, td {{ text-align:left; padding:10px 12px; border-bottom:1px solid #262a33; font-size:0.9rem; }}
  th {{ color:#9aa0a6; font-weight:600; position:sticky; top:0; background:#0f1115; cursor:pointer; }}
  tr.deal {{ background:#16321f; }}
  tr.deal td:nth-child(4) {{ color:#4ade80; font-weight:600; }}
  a {{ color:#8ab4f8; text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  .legend {{ margin-bottom:16px; font-size:0.85rem; color:#9aa0a6; }}
  .dot {{ display:inline-block; width:10px; height:10px; background:#16321f; border:1px solid #4ade80; margin-right:6px; vertical-align:middle; }}
</style>
</head>
<body>
  <h1>🏍️ Dirt Bike Deal Finder</h1>
  <div class="meta">Last updated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} &middot; {len(all_listings)} listings tracked &middot; {len(deals)} flagged as deals</div>
  <div class="legend"><span class="dot"></span> Flagged as a deal (priced well below similar bikes)</div>
  <table id="tbl">
    <thead>
      <tr><th>Listing</th><th>Price</th><th>Group median</th><th>Deal signal</th><th>Source</th><th>Group</th></tr>
    </thead>
    <tbody>
      {''.join(rows_html)}
    </tbody>
  </table>
<script>
document.querySelectorAll('th').forEach((th, idx) => {{
  th.addEventListener('click', () => {{
    const tbody = document.querySelector('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));
    const asc = th.dataset.asc = th.dataset.asc === '1' ? '0' : '1';
    rows.sort((a, b) => {{
      const av = a.children[idx].innerText, bv = b.children[idx].innerText;
      return asc === '1' ? av.localeCompare(bv, undefined, {{numeric:true}}) : bv.localeCompare(av, undefined, {{numeric:true}});
    }});
    rows.forEach(r => tbody.appendChild(r));
  }});
}});
</script>
</body>
</html>"""
    OUTPUT_HTML.write_text(html)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    history = load_history()
    fresh = []

    for term in SEARCH_TERMS:
        fresh.extend(fetch_craigslist(term))
        time.sleep(1)
        fresh.extend(fetch_ksl(term))
        time.sleep(1)

    # filter by price range
    def in_range(item):
        if item["price"] is None:
            return False
        if item["price"] < MIN_PRICE:
            return False
        if MAX_PRICE is not None and item["price"] > MAX_PRICE:
            return False
        return True

    fresh = [i for i in fresh if in_range(i)]

    history = merge_into_history(history, fresh)
    save_history(history)

    all_listings = list(history.values())
    all_listings = score_listings(all_listings)

    OUTPUT_JSON.write_text(json.dumps(all_listings, indent=2))
    render_dashboard(all_listings)

    print(f"Scraped {len(fresh)} fresh listings this run.")
    print(f"Tracking {len(all_listings)} total listings.")
    print(f"Flagged {sum(1 for i in all_listings if i['is_deal'])} as deals.")


if __name__ == "__main__":
    main()
