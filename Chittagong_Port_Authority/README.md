# Chittagong Port Authority — Dashboard Scraper & Analytics

Scrapes the public **Terminal Operating System (TOS)** dashboard and the
**Port Community System (PCS)** historical reports published by the
Chittagong (Chattogram) Port Authority at **https://cpatos.gov.bd**, stores
everything as tidy CSVs, and turns the data into an analyst-grade pack of
charts and a written insights report.

```
Chittagong_Port_Authority/
├── cpa_scraper.py        # scrapes TOS + PCS into outputs/ (incremental)
├── cpa_analysis.py       # builds charts + insights.md from the CSVs
├── make_sample_data.py   # generates SYNTHETIC demo data (see note below)
├── requirements.txt
├── README.md
└── outputs/              # all data + analysis land here
    ├── snapshots/        # one timestamped row-set per live page, per run
    ├── <series>_daily.csv# one row-set per calendar day (historical reports)
    ├── analysis/         # charts (.png) + monthly_summary.csv + insights.md
    └── sample/           # SYNTHETIC demo data + its analysis (delete later)
```

---

## Quick start

```bash
pip install -r requirements.txt

# 1) Scrape (incremental — safe to run on a schedule)
python cpa_scraper.py

# 2) Analyse what you've scraped
python cpa_analysis.py            # reads ./outputs, writes ./outputs/analysis
```

> **Want to see the output before scraping?** This was built in a sandbox
> that cannot reach `cpatos.gov.bd`, so a synthetic demo is included:
> ```bash
> python make_sample_data.py
> python cpa_analysis.py --data-dir outputs/sample
> ```
> The charts in `outputs/sample/analysis/` are produced from **fake** data
> and exist only to show the pipeline working. Delete `outputs/sample/`
> once you have real data.

---

## The two kinds of data (and why scraping differs)

The CPA site publishes two fundamentally different things:

| Kind | Pages | What it is | How we scrape it |
|------|-------|-----------|------------------|
| **Real-time snapshots** | `tosdashboard/*.php` | The state of the port *right now* (which vessels are at which berth, current yard/berth occupancy, equipment positions). Overwritten continuously. | Each run appends **one timestamped snapshot** per page. Over time this builds a history of the live state. |
| **Daily historical reports** | `pcs/index.php/report/.../YYYY-MM-DD` | A *finalised* report for a specific past calendar day. | Scrape **day-by-day from a start date forward**, remember the last day reached, and on the next run only fetch new days. |

This is why the scraper has two engines. The daily-report engine is the one
that satisfies *"scrape up to April now, only grab May next time and append it."*

---

## Incremental behaviour (how "append only new data" works)

- **Daily reports** — the scraper keeps a cursor in
  `outputs/.scrape_state.json` (the last calendar day it processed). The next
  run resumes from *cursor + 1 day* through *today* and appends only the new
  days to `outputs/<series>_daily.csv`. Days with no published report are
  remembered as "empty" so they aren't re-fetched forever.
- **Snapshots** — each run appends the current snapshot, de-duplicated on the
  page's own *"as of"* timestamp, so running twice inside one publish window
  won't create duplicate rows.
- All writes are *load existing → add new → de-duplicate → write*, so history
  is never lost and re-runs are safe (idempotent).

Useful flags:

```bash
python cpa_scraper.py --only snapshots       # only the live pages
python cpa_scraper.py --only date-series     # only the daily historical reports
python cpa_scraper.py --from 2021-01-01      # force the daily start date
python cpa_scraper.py --to   2026-04-30      # stop the daily scrape here
python cpa_scraper.py --refresh-days 7       # also re-pull the last 7 days
python cpa_scraper.py --reset                # ignore cursors, start from scratch
python cpa_scraper.py --save-html            # keep raw HTML for debugging
```

Schedule it (e.g. daily) with cron / Task Scheduler / a GitHub Action and the
dataset grows itself.

---

## Datasets — what each file means

### Real-time snapshots — `outputs/snapshots/`

Every file carries four metadata columns we add: `scraped_at` (when we
fetched), `as_of` (the timestamp the page itself prints), `source_url`, and
`table_index` (which table on the page, for pages with more than one). The
remaining columns are whatever the port publishes, kept verbatim.

| File | Source page | What it tells you |
|------|-------------|-------------------|
| `container_vessel_overview.csv` | `index.php` | Headline container-vessel position overview. |
| `vessel_at_berth_container.csv` | `vesselAtBerth.php` | Which container vessels are at which berth right now, with import/export/restow volumes and operating status. |
| `vessel_at_berth_breakbulk.csv` | `vesselAtBerth_BreakBulk.php` | Same, for break-bulk / general-cargo vessels. |
| `vessel_report.csv` | `vesselReport.php` | Vessel-level report (arrivals/operations). |
| `berth_occupancy.csv` | `berthOccupance.php` | Berth occupancy — how many berths are occupied vs free. |
| `yard_occupancy.csv` | `yardOccupance.php` | Container-yard occupancy by zone (capacity vs occupied, %). |
| `day_wise_lying_container.csv` | `day_wise_lying_container.php` | Containers "lying" in the yard (yard stock), day-wise. |
| `performance_24h.csv` | `24HoursperformanceReport.php` | Rolling 24-hour operational performance (handling/productivity). |
| `equipment_current_status.csv` | `mis_equipment_current_status.php` | Container-handling equipment position across zones AB, C, D & PICT. |

### Daily historical reports — `outputs/*_daily.csv`

| File | Source | What it tells you |
|------|--------|-------------------|
| `container_handling_daily.csv` | `pcs/index.php/report/containerHandlingView/YYYY-MM-DD` | One finalised record per calendar day: container handling / yard-wise equipment booking. Carries a `report_date` column — the time-series spine for the analysis. Data is available back to at least 2021. |

> Column names below the metadata columns are taken **as published**. If the
> port renames a column, you don't need to touch the scraper — just update the
> `CANON` mapping at the top of `cpa_analysis.py` so the analysis keeps finding
> the right metric.

### Analysis outputs — `outputs/analysis/`

| File | What it is |
|------|-----------|
| `00_dashboard.png` | One-page executive dashboard (throughput, trade mix, utilisation, efficiency). |
| `01_throughput_monthly.png` | Monthly TEUs handled + 3-month moving average. |
| `02_throughput_yoy.png` | Year-on-year throughput growth (%). |
| `03_trade_mix.png` | Import vs export TEUs over time. |
| `04_utilisation.png` | Yard & berth occupancy vs congestion thresholds. |
| `05_efficiency.png` | Vessel turnaround vs crane productivity. |
| `06_congestion_scatter.png` | Dwell time vs yard occupancy (the cost of congestion). |
| `07_seasonality_heatmap.png` | Monthly throughput by year (seasonality). |
| `08_berth_snapshot.png` | Current berth-by-berth container load. |
| `monthly_summary.csv` | The monthly KPI table behind the charts. |
| `insights.md` | Written, analyst-style read of the numbers. |

---

## What the analysis looks at (the "analyst" lens)

- **Growth** — monthly throughput, 3-month trend, CAGR, trailing-12-month vs
  prior year, and YoY momentum. (The incomplete trailing month is dropped so
  growth isn't understated by a half-month of data.)
- **Trade mix** — import vs export split and the empty-container share of
  exports (a structural feature of an import-skewed gateway).
- **Capacity & congestion** — yard and berth occupancy against the
  conventional ~80% / ~85% congestion lines, plus vessels waiting at outer
  anchorage as a queue-pressure lead indicator.
- **Efficiency** — vessel turnaround hours and crane gross-moves-per-hour, and
  the empirical relationship between yard occupancy and container dwell time.

---

## Notes, limits & etiquette

- **Be polite to a government server.** The scraper throttles requests
  (`THROTTLE` in `cpa_scraper.py`) and retries with exponential backoff. The
  *first* historical run can hit thousands of dates — run it once, off-peak,
  then let the daily incremental top-ups do the rest.
- **Structure-agnostic parsing.** Tables are read with `pandas.read_html` and a
  BeautifulSoup fallback, so the scraper survives column re-ordering/renaming.
  If a page is JavaScript-rendered and exposes no `<table>`, it will report
  "no tables found" — capture it with `--save-html` and inspect.
- **This is public data** scraped for research/analysis. Respect the site's
  terms of use.
