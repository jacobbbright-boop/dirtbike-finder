# Dirt Bike Deal Finder

Scrapes KSL Classifieds and Craigslist for used dirt bikes, flags listings
priced well below similar bikes, and publishes a dashboard you can check
from your phone or computer — no server of your own required.

## What it does

- Searches KSL + Craigslist for a list of keywords (dirt bike, motocross, etc.)
- Groups similar bikes together (by model like "YZ250" or by displacement)
- Flags any listing priced 20%+ below the median for its group as a **deal**
- Builds a `dashboard.html` you open in a browser, sortable by column
- Remembers every listing it's seen (`data/listings_history.json`) so the
  price baseline gets smarter the longer it runs

## Facebook Marketplace

Not included. FB requires a logged-in session and actively blocks
automated scraping — there's no reliable, ToS-compliant way to script it.
Treat it as a manual check, or search for a licensed data provider if you
really want it automated.

## Option A — run it yourself, locally (fastest to try)

```bash
pip install -r requirements.txt
python scraper.py
```

Then open `dashboard.html` in your browser. Run it again anytime (e.g. via
cron or Task Scheduler) to refresh.

## Option B — fully automated, free, no computer needed (recommended)

This uses GitHub Actions to run the scraper every 4 hours and GitHub Pages
to host the dashboard as a webpage you can bookmark.

1. Create a new GitHub repo and push this folder to it.
2. In the repo, go to **Settings → Pages** → set source to **Deploy from a
   branch**, branch `main`, folder `/ (root)`. Save.
3. Go to **Settings → Actions → General → Workflow permissions** and select
   **"Read and write permissions"** (so the workflow can commit updates).
4. Go to the **Actions** tab, select "Scrape dirt bike listings," and click
   **Run workflow** once to test it.
5. Your dashboard will be live at:
   `https://<your-username>.github.io/<repo-name>/dashboard.html`

It'll auto-update every 4 hours from then on. Change the cron schedule in
`.github/workflows/run.yml` if you want it more/less frequent.

## Tuning it to your search

Edit the top of `scraper.py`:

- `CRAIGSLIST_REGION` — set to your local Craigslist subdomain
- `SEARCH_TERMS` — add specific models you want, e.g. `"yz250f"`, `"crf450"`
- `MIN_PRICE` / `MAX_PRICE` — your budget range
- `DEAL_THRESHOLD_PCT` — how far below median counts as a "deal" (default 20%)

## A heads-up on fragility

Both sites can change their HTML at any time, which breaks the CSS
selectors in `fetch_craigslist()` / `fetch_ksl()`. KSL in particular
sometimes blocks plain scraping outright (its site is a JS app with bot
protection) — if `dashboard.html` stops filling up with KSL results,
that's the likely cause. The fix is either:

- Inspect the site in your browser's dev tools and update the CSS
  selectors in `scraper.py`, or
- Swap the `fetch_ksl()`/`fetch_craigslist()` function bodies for a
  headless-browser fetch (Playwright) or a paid scraping API (e.g. Apify
  has an existing KSL scraper) — just keep the same return shape
  (list of dicts with title/price/url/source).
