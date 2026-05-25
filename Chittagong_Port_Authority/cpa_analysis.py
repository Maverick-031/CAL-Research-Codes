"""
Chittagong Port Authority — Data Analysis & Visualisation
=========================================================
Reads the CSVs produced by cpa_scraper.py and turns them into an
analyst-grade pack: a multi-panel dashboard, a set of standalone charts,
a tidy monthly KPI table, and a written insights report (insights.md).

The thinking is deliberately "sell-side analyst": throughput growth and
CAGR, year-on-year momentum, the import/export and full/empty mix,
yard & berth utilisation versus congestion thresholds, vessel turnaround
and crane productivity as efficiency proxies, and the relationship
between yard congestion and container dwell time.

COLUMN MAPPING
  Real-world column headers are messy and may change. Instead of hard-
  coding them, every metric is resolved through CANON below: a canonical
  name -> list of possible header spellings (case-insensitive substring
  match). Add your real column names here once and everything downstream
  works. Charts whose inputs are missing are skipped and noted in the
  report rather than crashing.

USAGE
  python cpa_analysis.py                                   # uses ./outputs
  python cpa_analysis.py --data-dir outputs/sample         # demo on sample
  python cpa_analysis.py --data-dir outputs --out-dir outputs/analysis
"""

import argparse
import sys
from pathlib import Path

try:
    import numpy as np
    import pandas as pd
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mtick
    from matplotlib.dates import DateFormatter
except ImportError:
    sys.exit("Missing deps. Run:  pip install -r requirements.txt")


# ── Canonical metric -> candidate column-header spellings ────
CANON = {
    "date":              ["report_date", "date", "as_of", "scraped_at"],
    "total_teus":        ["total_teus", "total teus", "grand total", "total container", "total"],
    "import_teus":       ["total_import_teus", "import_teus", "import teus", "import"],
    "export_teus":       ["total_export_teus", "export_teus", "export teus", "export"],
    "import_full_teus":  ["import_full_teus", "import full"],
    "import_empty_teus": ["import_empty_teus", "import empty"],
    "export_full_teus":  ["export_full_teus", "export full"],
    "export_empty_teus": ["export_empty_teus", "export empty"],
    "yard_occupancy_pct":["yard_occupancy_pct", "yard occupancy", "occupancy_pct", "occupancy %"],
    "yard_stock_teus":   ["yard_stock_teus", "yard stock", "lying", "ground"],
    "berth_occupancy_pct":["berth_occupancy_pct", "berth occupancy"],
    "avg_dwell_days":    ["avg_dwell_days", "dwell", "dwell_days"],
    "avg_turnaround_hours":["avg_turnaround_hours", "turnaround", "turn round", "stay hours"],
    "gross_moves_per_hour":["gross_moves_per_hour", "moves_per_hour", "mph", "berth moves per hour", "productivity"],
    "vessels_at_berth":  ["vessels_at_berth", "at berth", "berthed"],
    "vessels_arrived":   ["vessels_arrived", "arrived", "arrival"],
    "vessels_at_outer_anchorage": ["vessels_at_outer_anchorage", "anchorage", "waiting"],
}

CONGESTION_YARD = 80.0     # % yard occupancy widely treated as congested
CONGESTION_BERTH = 85.0    # % berth occupancy treated as saturated


# ═══════════════════════════════════════════════════════════
#  STYLE
# ═══════════════════════════════════════════════════════════
NAVY, BLUE, TEAL, AMBER, RED, GREY = (
    "#1b2a4a", "#2f6fb0", "#1f9e89", "#e1a23b", "#c0392b", "#8a8d91")


def set_style():
    plt.rcParams.update({
        "figure.dpi": 120, "savefig.dpi": 150,
        "font.size": 10, "font.family": "DejaVu Sans",
        "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlecolor": NAVY,
        "axes.labelsize": 9.5, "axes.edgecolor": "#cccccc",
        "axes.grid": True, "grid.color": "#e8e8e8", "grid.linewidth": 0.8,
        "axes.spines.top": False, "axes.spines.right": False,
        "legend.frameon": False, "legend.fontsize": 8.5,
        "figure.facecolor": "white", "axes.facecolor": "white",
    })


def _footer(fig, data_dir):
    fig.text(0.01, 0.005,
             f"Source: Chittagong Port Authority TOS/PCS dashboards (cpatos.gov.bd)  •  "
             f"data: {data_dir}", fontsize=7, color=GREY)


# ═══════════════════════════════════════════════════════════
#  LOADING / COLUMN RESOLUTION
# ═══════════════════════════════════════════════════════════
def find_col(df: pd.DataFrame, candidates: list[str]):
    low = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in low:                       # exact (normalised)
            return low[cand.lower()]
    for cand in candidates:                           # substring
        for c in df.columns:
            if cand.lower() in c.lower():
                return c
    return None


def numeric(s: pd.Series) -> pd.Series:
    return pd.to_numeric(
        s.astype(str).str.replace(r"[,%\s]", "", regex=True).replace("", np.nan),
        errors="coerce")


def load_daily(data_dir: Path) -> pd.DataFrame | None:
    """Find the main daily time-series CSV and standardise its columns."""
    candidates = list(data_dir.glob("*_daily.csv")) + list(data_dir.glob("*daily*.csv"))
    if not candidates:
        return None
    path = max(candidates, key=lambda p: p.stat().st_size)
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    date_col = find_col(df, CANON["date"])
    if not date_col:
        return None
    out = pd.DataFrame()
    out["date"] = pd.to_datetime(df[date_col], errors="coerce", dayfirst=False)
    if out["date"].isna().mean() > 0.5:
        out["date"] = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True)
    for canon, cands in CANON.items():
        if canon == "date":
            continue
        col = find_col(df, cands)
        if col is not None:
            out[canon] = numeric(df[col])
    out = out.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    out.attrs["source_file"] = path.name
    # derive total if absent but parts present
    if "total_teus" not in out and {"import_teus", "export_teus"} <= set(out.columns):
        out["total_teus"] = out["import_teus"].fillna(0) + out["export_teus"].fillna(0)
    return out


def load_snapshot(data_dir: Path, key: str) -> pd.DataFrame | None:
    p = data_dir / "snapshots" / f"{key}.csv"
    if not p.exists():
        return None
    return pd.read_csv(p, dtype=str, keep_default_na=False)


# ═══════════════════════════════════════════════════════════
#  ANALYSIS HELPERS
# ═══════════════════════════════════════════════════════════
SUM_METRICS = ["total_teus", "import_teus", "export_teus", "import_full_teus",
               "import_empty_teus", "export_full_teus", "export_empty_teus"]
MEAN_METRICS = ["yard_occupancy_pct", "berth_occupancy_pct", "avg_dwell_days",
                "avg_turnaround_hours", "gross_moves_per_hour", "vessels_at_berth",
                "vessels_at_outer_anchorage"]


def monthly(df: pd.DataFrame) -> pd.DataFrame:
    """Resample to month-start. The trailing month is dropped if incomplete,
    so flow (sum) metrics like throughput aren't understated by a half-month."""
    g = df.set_index("date")
    agg = {c: "sum" for c in SUM_METRICS if c in df.columns}
    agg.update({c: "mean" for c in MEAN_METRICS if c in df.columns})
    m = g.resample("MS").agg(agg)
    last_day = df["date"].max()
    if len(m) and last_day.day < last_day.days_in_month:
        m = m.iloc[:-1]                       # drop the partial final month
        m.attrs["dropped_partial_month"] = last_day.strftime("%b %Y")
    return m


def cagr(series: pd.Series) -> float | None:
    s = series.dropna()
    if len(s) < 13 or s.iloc[0] <= 0:
        return None
    years = (s.index[-1] - s.index[0]).days / 365.25
    if years <= 0:
        return None
    return (s.iloc[-1] / s.iloc[0]) ** (1 / years) - 1


def fmt_int(x):
    return f"{x:,.0f}"


# ═══════════════════════════════════════════════════════════
#  CHARTS
# ═══════════════════════════════════════════════════════════
def chart_throughput(m, out_dir, data_dir):
    if "total_teus" not in m:
        return None
    s = m["total_teus"].dropna()
    if s.empty:
        return None
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.bar(s.index, s.values, width=20, color=BLUE, alpha=0.55, label="Monthly TEUs")
    ma = s.rolling(3, min_periods=1).mean()
    ax.plot(ma.index, ma.values, color=NAVY, lw=2.2, label="3-month moving avg")
    ax.yaxis.set_major_formatter(mtick.FuncFormatter(lambda v, _: f"{v/1000:.0f}k"))
    ax.set_title("Container Throughput — Monthly TEUs Handled")
    ax.set_ylabel("TEUs")
    ax.legend(loc="upper left")
    ax.xaxis.set_major_formatter(DateFormatter("%b\n%Y"))
    _footer(fig, data_dir)
    p = out_dir / "01_throughput_monthly.png"
    fig.tight_layout(rect=[0, 0.02, 1, 1]); fig.savefig(p); plt.close(fig)
    return p


def chart_yoy(m, out_dir, data_dir):
    if "total_teus" not in m:
        return None
    s = m["total_teus"].dropna()
    yoy = s.pct_change(12) * 100
    yoy = yoy.dropna()
    if yoy.empty:
        return None
    fig, ax = plt.subplots(figsize=(11, 4.2))
    colors = [TEAL if v >= 0 else RED for v in yoy.values]
    ax.bar(yoy.index, yoy.values, width=20, color=colors)
    ax.axhline(0, color=GREY, lw=1)
    ax.yaxis.set_major_formatter(mtick.PercentFormatter())
    ax.set_title("Throughput Momentum — Year-on-Year Growth (%)")
    ax.set_ylabel("YoY %")
    ax.xaxis.set_major_formatter(DateFormatter("%b\n%Y"))
    _footer(fig, data_dir)
    p = out_dir / "02_throughput_yoy.png"
    fig.tight_layout(rect=[0, 0.02, 1, 1]); fig.savefig(p); plt.close(fig)
    return p


def chart_trade_mix(m, out_dir, data_dir):
    if not {"import_teus", "export_teus"} <= set(m.columns):
        return None
    imp, exp = m["import_teus"].fillna(0), m["export_teus"].fillna(0)
    if (imp + exp).sum() == 0:
        return None
    fig, ax = plt.subplots(figsize=(11, 4.6))
    ax.stackplot(m.index, imp.values, exp.values,
                 labels=["Import", "Export"], colors=[BLUE, AMBER], alpha=0.85)
    ax.yaxis.set_major_formatter(mtick.FuncFormatter(lambda v, _: f"{v/1000:.0f}k"))
    ax.set_title("Trade Mix — Import vs Export TEUs (monthly)")
    ax.set_ylabel("TEUs"); ax.legend(loc="upper left")
    ax.xaxis.set_major_formatter(DateFormatter("%b\n%Y"))
    _footer(fig, data_dir)
    p = out_dir / "03_trade_mix.png"
    fig.tight_layout(rect=[0, 0.02, 1, 1]); fig.savefig(p); plt.close(fig)
    return p


def chart_utilisation(m, out_dir, data_dir):
    cols = [c for c in ["yard_occupancy_pct", "berth_occupancy_pct"] if c in m.columns]
    if not cols:
        return None
    fig, ax = plt.subplots(figsize=(11, 4.6))
    if "yard_occupancy_pct" in m:
        ax.plot(m.index, m["yard_occupancy_pct"], color=TEAL, lw=2, label="Yard occupancy")
        ax.axhline(CONGESTION_YARD, color=TEAL, ls="--", lw=1, alpha=0.6)
    if "berth_occupancy_pct" in m:
        ax.plot(m.index, m["berth_occupancy_pct"], color=RED, lw=2, label="Berth occupancy")
        ax.axhline(CONGESTION_BERTH, color=RED, ls="--", lw=1, alpha=0.6)
    ax.yaxis.set_major_formatter(mtick.PercentFormatter())
    ax.set_ylim(0, 100)
    ax.set_title("Capacity Utilisation — Yard & Berth Occupancy (dashed = congestion line)")
    ax.set_ylabel("%"); ax.legend(loc="lower left")
    ax.xaxis.set_major_formatter(DateFormatter("%b\n%Y"))
    _footer(fig, data_dir)
    p = out_dir / "04_utilisation.png"
    fig.tight_layout(rect=[0, 0.02, 1, 1]); fig.savefig(p); plt.close(fig)
    return p


def chart_efficiency(m, out_dir, data_dir):
    has_turn = "avg_turnaround_hours" in m.columns
    has_mph = "gross_moves_per_hour" in m.columns
    if not (has_turn or has_mph):
        return None
    fig, ax = plt.subplots(figsize=(11, 4.6))
    lines = []
    if has_turn:
        l1, = ax.plot(m.index, m["avg_turnaround_hours"], color=NAVY, lw=2,
                      label="Vessel turnaround (hrs)")
        lines.append(l1); ax.set_ylabel("Turnaround (hours)", color=NAVY)
    if has_mph:
        ax2 = ax.twinx() if has_turn else ax
        l2, = ax2.plot(m.index, m["gross_moves_per_hour"], color=AMBER, lw=2,
                       label="Crane moves/hour")
        lines.append(l2)
        ax2.set_ylabel("Gross moves / hour", color=AMBER)
        ax2.grid(False)
    ax.set_title("Operational Efficiency — Turnaround vs Crane Productivity")
    ax.legend(handles=lines, loc="upper left")
    ax.xaxis.set_major_formatter(DateFormatter("%b\n%Y"))
    _footer(fig, data_dir)
    p = out_dir / "05_efficiency.png"
    fig.tight_layout(rect=[0, 0.02, 1, 1]); fig.savefig(p); plt.close(fig)
    return p


def chart_congestion_scatter(df, out_dir, data_dir):
    if not {"yard_occupancy_pct", "avg_dwell_days"} <= set(df.columns):
        return None
    d = df[["yard_occupancy_pct", "avg_dwell_days"]].dropna()
    if len(d) < 10:
        return None
    fig, ax = plt.subplots(figsize=(7, 5.5))
    ax.scatter(d["yard_occupancy_pct"], d["avg_dwell_days"], s=12, color=BLUE, alpha=0.35)
    # linear fit
    coef = np.polyfit(d["yard_occupancy_pct"], d["avg_dwell_days"], 1)
    xs = np.linspace(d["yard_occupancy_pct"].min(), d["yard_occupancy_pct"].max(), 50)
    ax.plot(xs, np.polyval(coef, xs), color=RED, lw=2,
            label=f"fit: +{coef[0]:.2f} days per +1% occ.")
    ax.axvline(CONGESTION_YARD, color=GREY, ls="--", lw=1)
    ax.set_xlabel("Yard occupancy (%)"); ax.set_ylabel("Avg container dwell (days)")
    ax.set_title("Congestion Cost — Dwell Time vs Yard Occupancy")
    ax.legend(loc="upper left")
    _footer(fig, data_dir)
    p = out_dir / "06_congestion_scatter.png"
    fig.tight_layout(rect=[0, 0.02, 1, 1]); fig.savefig(p); plt.close(fig)
    return p


def chart_seasonality(m, out_dir, data_dir):
    if "total_teus" not in m:
        return None
    s = m["total_teus"].dropna()
    if len(s) < 13:
        return None
    piv = s.to_frame("teus")
    piv["year"] = piv.index.year
    piv["month"] = piv.index.month
    table = piv.pivot_table(index="year", columns="month", values="teus")
    fig, ax = plt.subplots(figsize=(10, max(3, 0.6 * len(table) + 1.5)))
    im = ax.imshow(table.values, aspect="auto", cmap="YlGnBu")
    ax.set_xticks(range(12)); ax.set_xticklabels(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])
    ax.set_yticks(range(len(table.index))); ax.set_yticklabels(table.index)
    ax.set_title("Seasonality — Monthly TEUs by Year")
    cb = fig.colorbar(im, ax=ax, shrink=0.8)
    cb.set_label("TEUs")
    _footer(fig, data_dir)
    p = out_dir / "07_seasonality_heatmap.png"
    fig.tight_layout(rect=[0, 0.02, 1, 1]); fig.savefig(p); plt.close(fig)
    return p


def chart_berth_snapshot(data_dir, out_dir):
    snap = load_snapshot(data_dir, "vessel_at_berth_container")
    if snap is None or snap.empty:
        return None
    berth_col = find_col(snap, ["berth"])
    status_col = find_col(snap, ["status"])
    imp_col = find_col(snap, ["import_teus", "import"])
    exp_col = find_col(snap, ["export_teus", "export"])
    if not berth_col or not (imp_col or exp_col):
        return None
    df = snap.copy()
    imp = numeric(df[imp_col]) if imp_col else pd.Series(0, index=df.index)
    exp = numeric(df[exp_col]) if exp_col else pd.Series(0, index=df.index)
    order = (imp + exp).sort_values(ascending=True).index
    fig, ax = plt.subplots(figsize=(9, max(3, 0.4 * len(df) + 1.5)))
    ax.barh(df[berth_col][order], imp[order], color=BLUE, label="Import TEUs")
    ax.barh(df[berth_col][order], exp[order], left=imp[order], color=AMBER, label="Export TEUs")
    as_of = snap["as_of"].iloc[0] if "as_of" in snap.columns else ""
    ax.set_title(f"Current Berth Snapshot — Container Operation\n(as of {as_of})")
    ax.set_xlabel("TEUs on board / planned"); ax.legend(loc="lower right")
    _footer(fig, data_dir)
    p = out_dir / "08_berth_snapshot.png"
    fig.tight_layout(rect=[0, 0.02, 1, 1]); fig.savefig(p); plt.close(fig)
    return p


def dashboard(m, df, out_dir, data_dir):
    """One-page executive dashboard combining the key panels."""
    fig, axes = plt.subplots(2, 2, figsize=(15, 9))
    fig.suptitle("Chittagong Port Authority — Operations Dashboard",
                 fontsize=16, fontweight="bold", color=NAVY)

    ax = axes[0, 0]
    if "total_teus" in m:
        s = m["total_teus"].dropna()
        ax.bar(s.index, s.values, width=20, color=BLUE, alpha=0.55)
        ax.plot(s.index, s.rolling(3, min_periods=1).mean(), color=NAVY, lw=2)
        ax.yaxis.set_major_formatter(mtick.FuncFormatter(lambda v, _: f"{v/1000:.0f}k"))
    ax.set_title("Monthly TEUs + 3M MA")

    ax = axes[0, 1]
    if {"import_teus", "export_teus"} <= set(m.columns):
        ax.stackplot(m.index, m["import_teus"].fillna(0), m["export_teus"].fillna(0),
                     colors=[BLUE, AMBER], alpha=0.85, labels=["Import", "Export"])
        ax.legend(loc="upper left")
        ax.yaxis.set_major_formatter(mtick.FuncFormatter(lambda v, _: f"{v/1000:.0f}k"))
    ax.set_title("Import vs Export")

    ax = axes[1, 0]
    if "yard_occupancy_pct" in m:
        ax.plot(m.index, m["yard_occupancy_pct"], color=TEAL, lw=2, label="Yard")
    if "berth_occupancy_pct" in m:
        ax.plot(m.index, m["berth_occupancy_pct"], color=RED, lw=2, label="Berth")
    ax.axhline(CONGESTION_YARD, color=GREY, ls="--", lw=1)
    ax.set_ylim(0, 100); ax.yaxis.set_major_formatter(mtick.PercentFormatter())
    ax.set_title("Utilisation %"); ax.legend(loc="lower left")

    ax = axes[1, 1]
    if "avg_turnaround_hours" in m:
        ax.plot(m.index, m["avg_turnaround_hours"], color=NAVY, lw=2, label="Turnaround hrs")
    if "gross_moves_per_hour" in m:
        ax2 = ax.twinx()
        ax2.plot(m.index, m["gross_moves_per_hour"], color=AMBER, lw=2)
        ax2.set_ylabel("Moves/hr", color=AMBER); ax2.grid(False)
    ax.set_title("Efficiency")

    for a in axes.flat:
        a.xaxis.set_major_formatter(DateFormatter("%b\n%y"))
    _footer(fig, data_dir)
    p = out_dir / "00_dashboard.png"
    fig.tight_layout(rect=[0, 0.02, 1, 0.96]); fig.savefig(p); plt.close(fig)
    return p


# ═══════════════════════════════════════════════════════════
#  INSIGHTS REPORT
# ═══════════════════════════════════════════════════════════
def build_insights(df, m, out_dir, charts):
    L = []
    A = L.append
    A("# Chittagong Port Authority — Operations & Throughput Insights\n")
    A(f"_Generated {pd.Timestamp.now():%Y-%m-%d %H:%M} from `{df.attrs.get('source_file','?')}`._\n")
    A(f"_Coverage: {df['date'].min():%Y-%m-%d} to {df['date'].max():%Y-%m-%d} "
      f"({len(df):,} daily records)._\n")
    if m.attrs.get("dropped_partial_month"):
        A(f"_Monthly charts/stats exclude the incomplete trailing month "
          f"({m.attrs['dropped_partial_month']})._\n")
    A("\n> NOTE: If this was generated from `outputs/sample/`, the figures are "
      "SYNTHETIC and for pipeline demonstration only.\n")

    A("\n## 1. Throughput & growth\n")
    if "total_teus" in m:
        s = m["total_teus"].dropna()
        last12 = s.tail(12).sum()
        prev12 = s.iloc[-24:-12].sum() if len(s) >= 24 else np.nan
        A(f"- **Trailing-12-month throughput:** {fmt_int(last12)} TEUs.")
        if not np.isnan(prev12) and prev12 > 0:
            A(f"- **TTM vs prior 12 months:** {(last12/prev12-1)*100:+.1f}%.")
        g = cagr(s)
        if g is not None:
            A(f"- **Monthly-throughput CAGR over the window:** {g*100:+.1f}% p.a.")
        peak = s.idxmax()
        A(f"- **Peak month:** {peak:%b %Y} at {fmt_int(s.max())} TEUs; "
          f"**weakest:** {s.idxmin():%b %Y} at {fmt_int(s.min())} TEUs.")
        yoy = (s.pct_change(12) * 100).dropna()
        if not yoy.empty:
            A(f"- **Latest YoY momentum:** {yoy.iloc[-1]:+.1f}% "
              f"(12-month avg {yoy.tail(12).mean():+.1f}%).")
    else:
        A("- Total TEU column not found — map it in `CANON['total_teus']`.")

    A("\n## 2. Trade mix\n")
    if {"import_teus", "export_teus"} <= set(df.columns):
        imp, exp = df["import_teus"].sum(), df["export_teus"].sum()
        if imp + exp > 0:
            A(f"- **Import share:** {imp/(imp+exp)*100:.1f}%  •  "
              f"**Export share:** {exp/(imp+exp)*100:.1f}%  "
              f"(import/export ratio {imp/max(exp,1):.2f}).")
    if {"export_empty_teus", "export_full_teus"} <= set(df.columns):
        ee, ef = df["export_empty_teus"].sum(), df["export_full_teus"].sum()
        if ee + ef > 0:
            A(f"- **Empty share of exports:** {ee/(ee+ef)*100:.1f}% "
              "(high empty repositioning is typical of an import-skewed gateway).")

    A("\n## 3. Capacity utilisation & congestion\n")
    if "yard_occupancy_pct" in df.columns:
        y = df["yard_occupancy_pct"].dropna()
        share_cong = (y >= CONGESTION_YARD).mean() * 100
        A(f"- **Average yard occupancy:** {y.mean():.1f}% "
          f"(peak {y.max():.1f}%). Days at/above the {CONGESTION_YARD:.0f}% "
          f"congestion line: **{share_cong:.0f}%** of the period.")
    if "berth_occupancy_pct" in df.columns:
        b = df["berth_occupancy_pct"].dropna()
        A(f"- **Average berth occupancy:** {b.mean():.1f}% (peak {b.max():.1f}%).")
    if "vessels_at_outer_anchorage" in df.columns:
        wq = df["vessels_at_outer_anchorage"].dropna()
        A(f"- **Vessels waiting at outer anchorage:** {wq.mean():.0f} on average "
          f"(peak {wq.max():.0f}) — a lead indicator of berth queue pressure.")

    A("\n## 4. Operational efficiency\n")
    if "avg_turnaround_hours" in m.columns:
        t = m["avg_turnaround_hours"].dropna()
        if len(t) >= 13:
            A(f"- **Vessel turnaround:** {t.iloc[-1]:.0f} hrs latest vs "
              f"{t.iloc[0]:.0f} hrs at window start ({(t.iloc[-1]/t.iloc[0]-1)*100:+.0f}%).")
        else:
            A(f"- **Vessel turnaround:** {t.mean():.0f} hrs average.")
    if "gross_moves_per_hour" in m.columns:
        mph = m["gross_moves_per_hour"].dropna()
        A(f"- **Crane productivity:** {mph.mean():.1f} gross moves/hour average "
          f"(latest {mph.iloc[-1]:.1f}).")
    if {"yard_occupancy_pct", "avg_dwell_days"} <= set(df.columns):
        d = df[["yard_occupancy_pct", "avg_dwell_days"]].dropna()
        if len(d) > 10:
            slope = np.polyfit(d["yard_occupancy_pct"], d["avg_dwell_days"], 1)[0]
            corr = d.corr().iloc[0, 1]
            A(f"- **Congestion cost:** every +1pt of yard occupancy is associated "
              f"with **+{slope:.2f} days** of dwell (corr {corr:+.2f}).")

    A("\n## 5. Analyst takeaways\n")
    takeaways = []
    if "total_teus" in m:
        g = cagr(m["total_teus"].dropna())
        if g is not None:
            takeaways.append(
                f"Volume is compounding at ~{g*100:.0f}%/yr; the port's growth challenge "
                "is capacity, not demand.")
    if "yard_occupancy_pct" in df.columns and df["yard_occupancy_pct"].mean() >= 70:
        takeaways.append(
            "Yard utilisation runs structurally high — dwell-time discipline and "
            "off-dock/ICD evacuation are the key levers on effective capacity.")
    if "avg_turnaround_hours" in m.columns and len(m["avg_turnaround_hours"].dropna()) >= 13:
        t = m["avg_turnaround_hours"].dropna()
        if t.iloc[-1] > t.iloc[0]:
            takeaways.append(
                "Turnaround is drifting up as volumes rise — a sign berth/yard "
                "capacity is becoming the binding constraint.")
    if not takeaways:
        takeaways.append("Add more KPI columns (turnaround, occupancy, productivity) "
                         "to unlock the full takeaway set.")
    for t in takeaways:
        A(f"- {t}")

    A("\n## Figures\n")
    for c in charts:
        if c:
            A(f"- `{Path(c).name}`")

    (out_dir / "insights.md").write_text("\n".join(L), encoding="utf-8")
    return out_dir / "insights.md"


# ═══════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(description="Analyse Chittagong Port Authority data")
    ap.add_argument("--data-dir", default="outputs", help="folder with scraped CSVs")
    ap.add_argument("--out-dir", default=None, help="where to write charts/report")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir) if args.out_dir else data_dir / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)

    set_style()
    print(f"Reading from : {data_dir.resolve()}")
    print(f"Writing to   : {out_dir.resolve()}")

    df = load_daily(data_dir)
    if df is None or df.empty:
        sys.exit(f"No daily time-series CSV found in {data_dir}. "
                 "Run cpa_scraper.py first (or make_sample_data.py for a demo).")
    print(f"Loaded {len(df):,} daily rows from {df.attrs.get('source_file')} "
          f"with metrics: {[c for c in df.columns if c != 'date']}")

    m = monthly(df)
    m.to_csv(out_dir / "monthly_summary.csv", encoding="utf-8-sig")

    charts = [
        dashboard(m, df, out_dir, data_dir),
        chart_throughput(m, out_dir, data_dir),
        chart_yoy(m, out_dir, data_dir),
        chart_trade_mix(m, out_dir, data_dir),
        chart_utilisation(m, out_dir, data_dir),
        chart_efficiency(m, out_dir, data_dir),
        chart_congestion_scatter(df, out_dir, data_dir),
        chart_seasonality(m, out_dir, data_dir),
        chart_berth_snapshot(data_dir, out_dir),
    ]
    made = [c for c in charts if c]
    report = build_insights(df, m, out_dir, charts)

    print(f"\nCreated {len(made)} charts + monthly_summary.csv + {report.name}")
    for c in made:
        print(f"  - {Path(c).name}")


if __name__ == "__main__":
    main()
