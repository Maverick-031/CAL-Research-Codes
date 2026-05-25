"""
Synthetic sample-data generator  (FOR DEMONSTRATION ONLY)
=========================================================
This sandbox cannot reach cpatos.gov.bd, so this script fabricates a
*realistic but entirely fake* dataset that matches the schema the real
scraper produces. Its only purpose is to let you (and cpa_analysis.py)
see the full analysis pipeline working before you run the real scraper
on a machine with internet access.

It writes into  outputs/sample/  so it NEVER mixes with real scraped data
in  outputs/ .  Delete outputs/sample/ once you have real data.

  python make_sample_data.py
"""

import math
import random
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

random.seed(7)
np.random.seed(7)

SAMPLE_DIR = Path("outputs/sample")
SNAP_DIR = SAMPLE_DIR / "snapshots"

START = date(2023, 1, 1)
END = date(2026, 5, 24)


def build_daily_kpis() -> pd.DataFrame:
    """One finalised KPI row per calendar day — mimics the daily report."""
    days = (END - START).days + 1
    rows = []
    for i in range(days):
        d = START + timedelta(days=i)
        t = i / 365.25                                   # years since start
        # secular growth (~6%/yr), annual seasonality, weekly dip on Fri
        trend = 1.0 + 0.06 * t
        season = 1.0 + 0.10 * math.sin(2 * math.pi * (d.timetuple().tm_yday / 365.0))
        weekday = 0.86 if d.weekday() == 4 else 1.0      # Friday lull
        noise = np.random.normal(1.0, 0.06)
        base = 8200 * trend * season * weekday * noise   # total TEUs/day

        total_teus = max(2500, base)
        imp = total_teus * np.random.uniform(0.50, 0.56)
        exp = total_teus - imp
        imp_full = imp * np.random.uniform(0.93, 0.98)
        exp_full = exp * np.random.uniform(0.55, 0.70)   # many empties exported

        yard_capacity = 53000
        yard_stock = yard_capacity * np.clip(
            0.62 + 0.10 * season - 0.04 * weekday + np.random.normal(0, 0.05), 0.35, 0.99)
        berth_occ = np.clip(0.70 + 0.12 * season + np.random.normal(0, 0.05), 0.4, 0.99)

        rows.append({
            "report_date": d.isoformat(),
            "table_index": 1,
            "source_url": "SYNTHETIC",
            "vessels_arrived": int(np.random.poisson(11 * trend)),
            "vessels_departed": int(np.random.poisson(11 * trend)),
            "vessels_at_berth": int(np.clip(np.random.normal(14, 2), 6, 20)),
            "vessels_at_outer_anchorage": int(np.clip(np.random.normal(28, 9) * season, 2, 80)),
            "import_full_teus": round(imp_full, 1),
            "import_empty_teus": round(imp - imp_full, 1),
            "export_full_teus": round(exp_full, 1),
            "export_empty_teus": round(exp - exp_full, 1),
            "total_import_teus": round(imp, 1),
            "total_export_teus": round(exp, 1),
            "total_teus": round(total_teus, 1),
            "yard_stock_teus": round(yard_stock, 0),
            "yard_capacity_teus": yard_capacity,
            "yard_occupancy_pct": round(100 * yard_stock / yard_capacity, 1),
            "berth_occupancy_pct": round(100 * berth_occ, 1),
            "avg_dwell_days": round(np.clip(np.random.normal(4.2, 1.1) + (yard_stock / yard_capacity - 0.6) * 6, 1.5, 14), 2),
            "avg_turnaround_hours": round(np.clip(np.random.normal(56, 10) + berth_occ * 20, 30, 140), 1),
            "gross_moves_per_hour": round(np.clip(np.random.normal(28, 3) - (berth_occ - 0.7) * 6, 16, 40), 1),
        })
    return pd.DataFrame(rows)


def build_berth_snapshot() -> pd.DataFrame:
    """A single live snapshot like vesselAtBerth.php would show 'right now'."""
    berths = ["GCB-1", "GCB-2", "GCB-3", "GCB-4", "GCB-5",
              "NCT-1", "NCT-2", "NCT-3", "NCT-4", "NCT-5",
              "CCT-1", "CCT-2", "PCT-1", "PCT-2"]
    operators = ["SSA", "NCT", "CCT", "PCT", "BSA"]
    rows = []
    stamp = "2026-05-24T23:50:00"
    as_of = "24/05/2026 23:45"
    for b in berths:
        occupied = random.random() < 0.78
        rows.append({
            "scraped_at": stamp,
            "as_of": as_of,
            "source_url": "SYNTHETIC",
            "table_index": 1,
            "berth": b,
            "vessel_name": (random.choice(
                ["MV ", "MAERSK ", "MSC ", "CMA CGM ", "OOCL ", "X-PRESS ", "HMM "])
                + random.choice(["BENGAL", "CHATTOGRAM", "MERCURY", "VICTORY", "ATLAS",
                                 "PIONEER", "HARMONY", "EXPRESS"])) if occupied else "",
            "operator": random.choice(operators) if occupied else "",
            "import_teus": round(np.random.uniform(300, 1500), 0) if occupied else 0,
            "export_teus": round(np.random.uniform(200, 1300), 0) if occupied else 0,
            "restow_teus": round(np.random.uniform(0, 120), 0) if occupied else 0,
            "status": "Working" if occupied else "Vacant",
        })
    return pd.DataFrame(rows)


def build_yard_snapshot() -> pd.DataFrame:
    zones = ["Zone AB", "Zone C", "Zone D", "PICT", "NCT Yard", "CCT Yard"]
    rows = []
    stamp = "2026-05-24T23:50:00"
    as_of = "24/05/2026 23:45"
    for z in zones:
        cap = random.choice([6000, 8000, 9000, 12000])
        occ = round(cap * np.random.uniform(0.55, 0.95), 0)
        rows.append({
            "scraped_at": stamp, "as_of": as_of, "source_url": "SYNTHETIC",
            "table_index": 1, "yard_zone": z,
            "capacity_teus": cap, "occupied_teus": occ,
            "occupancy_pct": round(100 * occ / cap, 1),
        })
    return pd.DataFrame(rows)


def main():
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    daily = build_daily_kpis()
    daily.to_csv(SAMPLE_DIR / "container_handling_daily.csv", index=False, encoding="utf-8-sig")
    build_berth_snapshot().to_csv(SNAP_DIR / "vessel_at_berth_container.csv",
                                  index=False, encoding="utf-8-sig")
    build_yard_snapshot().to_csv(SNAP_DIR / "yard_occupancy.csv",
                                 index=False, encoding="utf-8-sig")
    print(f"Wrote synthetic sample data to {SAMPLE_DIR.resolve()}")
    print(f"  container_handling_daily.csv : {len(daily)} rows "
          f"({daily.report_date.min()} -> {daily.report_date.max()})")
    print("  snapshots/vessel_at_berth_container.csv, snapshots/yard_occupancy.csv")
    print("\nNOTE: This data is SYNTHETIC — for pipeline demonstration only.")


if __name__ == "__main__":
    main()
