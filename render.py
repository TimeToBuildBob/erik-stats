#!/usr/bin/env python3
"""Render charts/*.svg, data/summary.json and the README numbers table from data/monthly.csv.

Usage: uv run render.py

Output is deterministic (fixed SVG hash salt, no timestamps, glyphs as paths), so
re-rendering unchanged data produces no git diff.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter, MaxNLocator  # noqa: E402

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CHARTS = ROOT / "charts"
README = ROOT / "README.md"
START, END = "<!-- stats:start -->", "<!-- stats:end -->"

SURFACE, INK, INK_2, MUTED = "#fcfcfb", "#0b0b0b", "#52514e", "#898781"
GRID, BASELINE = "#e1e0d9", "#c3c2b7"
BLUE, ORANGE = "#2a78d6", "#e0782c"
BLUE_LIGHT, ORANGE_LIGHT = "#9ec5f4", "#f3c19b"

plt.rcParams.update(
    {
        "svg.hashsalt": "erik-stats",
        "svg.fonttype": "path",
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "axes.facecolor": SURFACE,
        "figure.facecolor": SURFACE,
        "axes.edgecolor": BASELINE,
        "axes.labelcolor": INK_2,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "xtick.labelcolor": INK_2,
        "ytick.labelcolor": INK_2,
        "axes.grid": True,
        "axes.grid.axis": "y",
        "grid.color": GRID,
        "grid.linewidth": 0.75,
        "axes.axisbelow": True,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.spines.left": False,
        "legend.frameon": False,
    }
)


def read_months() -> list[dict[str, int | str]]:
    with (DATA / "monthly.csv").open(newline="") as f:
        return [{k: (v if k == "month" else int(v)) for k, v in r.items()} for r in csv.DictReader(f)]


def month_date(m: str) -> dt.date:
    return dt.date(int(m[:4]), int(m[5:7]), 1)


def rolling(values: list[int], window: int = 3) -> list[float]:
    return [sum(values[max(0, i - window + 1) : i + 1]) / len(values[max(0, i - window + 1) : i + 1]) for i in range(len(values))]


def chart(name: str, title: str, subtitle: str, months: list, series: list[tuple[str, str, str]]) -> None:
    """series: (field, label, colour). Monthly values as pale bars, 3-month average as a line."""
    dates = [month_date(r["month"]) for r in months]
    fig, ax = plt.subplots(figsize=(8, 4))
    fig.subplots_adjust(left=0.09, right=0.97, top=0.78, bottom=0.12)
    fig.patch.set_edgecolor(GRID)
    fig.patch.set_linewidth(1.5)
    fig.text(0.02, 0.93, title, fontsize=14, fontweight="bold", color=INK)
    fig.text(0.02, 0.865, subtitle, fontsize=9.5, color=INK_2)
    for field, label, colour in series:
        vals = [r[field] for r in months]
        ax.plot(dates, rolling(vals), color=colour, linewidth=2, label=f"{label} (3-month avg)")
    ax.tick_params(axis="both", length=0, pad=6)
    ax.tick_params(axis="x", length=4, color=BASELINE)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.yaxis.set_major_locator(MaxNLocator(nbins=5, integer=True, min_n_ticks=3))
    loc = mdates.AutoDateLocator(minticks=4, maxticks=8)
    ax.xaxis.set_major_locator(loc)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc, show_offset=False))
    ax.set_xlim(dates[0], dates[-1])
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper left", fontsize=9)
    CHARTS.mkdir(exist_ok=True)
    fig.savefig(CHARTS / name, format="svg", metadata={"Date": None, "Creator": None})
    plt.close(fig)


def last_n(months: list, key: str, n: int) -> int:
    return sum(int(r[key]) for r in months[-n:])


def main() -> None:
    months = read_months()
    # Drop leading months before any activity so charts do not start on a flat line.
    active = [i for i, r in enumerate(months) if any(int(v) for k, v in r.items() if k != "month")]
    months = months[active[0] :]
    as_of = months[-1]["month"]
    # The newest month is partial, which would drag every 3-month average down.
    plotted = months[:-1]

    chart("comments.svg", "Issue and PR comments", "Public comments per month on GitHub issues and pull requests", plotted,
          [("issue_comments", "Comments", BLUE)])
    chart("prs.svg", "Pull requests", "Pull requests authored by Erik, by month opened and merged", plotted,
          [("prs_opened", "Opened", BLUE), ("prs_merged", "Merged", ORANGE)])
    chart("issues.svg", "Issues", "Issues authored by Erik, by month opened / closed", plotted,
          [("issues_opened", "Opened", BLUE), ("issues_closed", "Closed", ORANGE)])
    chart("commits.svg", "Commits and reviews", "Public commits and pull request reviews per month", plotted,
          [("commits", "Commits", BLUE), ("reviews", "Reviews", ORANGE)])

    fields = ["issue_comments", "prs_opened", "prs_merged", "prs_closed", "issues_opened", "issues_closed", "reviews", "commits"]
    summary = {
        "as_of_month": as_of,
        "all_time": {k: sum(int(r[k]) for r in months) for k in fields},
        "last_12_months": {k: last_n(months, k, 12) for k in fields},
        "last_month_complete": {k: int(months[-2][k]) for k in fields} if len(months) > 1 else {},
    }
    (DATA / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    labels = [
        ("issue_comments", "Issue and PR comments"),
        ("prs_opened", "Pull requests opened"),
        ("prs_merged", "Pull requests merged"),
        ("prs_closed", "Pull requests closed unmerged"),
        ("issues_opened", "Issues opened"),
        ("issues_closed", "Issues closed"),
        ("reviews", "Pull request reviews"),
        ("commits", "Commits"),
    ]
    rows = "\n".join(f"| {label} | {summary['last_12_months'][k]:,} | {summary['all_time'][k]:,} |" for k, label in labels)
    table = (
        f"{START}\n_Data through {as_of} (UTC; the current month is partial). "
        "Machine-readable: [`data/summary.json`](data/summary.json)._\n\n"
        f"| Metric | Last 12 months | All time |\n|---|---:|---:|\n{rows}\n{END}"
    )
    text = README.read_text()
    README.write_text(re.sub(f"{re.escape(START)}.*?{re.escape(END)}", lambda _: table, text, flags=re.S))


if __name__ == "__main__":
    main()
