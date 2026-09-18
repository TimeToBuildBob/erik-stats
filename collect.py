#!/usr/bin/env python3
"""Collect Erik's public GitHub activity into data/monthly.csv.

Usage: uv run collect.py [--full] [--months N]

Source of truth is GitHub's own GraphQL API, not the GH Archive event stream:
the archive drops events (about half of issue comments are missing) and stopped
recording the `merged` flag on PR events in 2024.

Only public repositories are counted, so the numbers reproduce with the
workflow's repo-scoped token as well as with a personal token.

By default the last `--months` months (default 3) are recomputed and older
months are kept; `--full` rebuilds every month since the account was created.
Files are written only after every request succeeded.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys
import time
from calendar import monthrange
from collections import Counter
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
MONTHLY_CSV = DATA / "monthly.csv"

USER = "ErikBjare"
GRAPHQL = "https://api.github.com/graphql"

FIELDS = [
    "month",
    "issue_comments",
    "prs_opened",
    "prs_merged",
    "prs_closed",
    "issues_opened",
    "issues_closed",
    "reviews",
    "commits",
]

# GraphQL aliases per request; keeps each query well under the node limit.
SEARCH_BATCH = 30
CONTRIB_BATCH = 6


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


class GitHub:
    def __init__(self) -> None:
        token = os.environ.get("STATS_GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not token:
            sys.exit("Set GITHUB_TOKEN (or GH_TOKEN / STATS_GH_TOKEN)")
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"bearer {token}", "User-Agent": "erik-stats"})

    def query(self, query: str, attempts: int = 6) -> dict:
        for attempt in range(attempts):
            wait = 2.0 * 2**attempt
            try:
                r = self.session.post(GRAPHQL, json={"query": query}, timeout=60)
            except requests.RequestException as e:
                log(f"network error ({e}); retry in {wait:.0f}s")
                time.sleep(wait)
                continue
            if r.status_code in (403, 429, 502, 503, 504):
                retry_after = r.headers.get("Retry-After")
                time.sleep(float(retry_after) if retry_after else wait)
                continue
            r.raise_for_status()
            body = r.json()
            errors = [e for e in body.get("errors", []) if e.get("type") != "NOT_FOUND"]
            if errors:
                if any("rate limit" in str(e).lower() or "timeout" in str(e).lower() for e in errors):
                    time.sleep(wait)
                    continue
                raise RuntimeError(f"GraphQL errors: {errors}")
            return body["data"]
        raise RuntimeError("GitHub GraphQL failed after retries")


def month_range(month: str) -> tuple[str, str]:
    year, mon = int(month[:4]), int(month[5:7])
    return f"{month}-01", f"{month}-{monthrange(year, mon)[1]:02d}"


def months_between(first: str, last: str) -> list[str]:
    y, m = int(first[:4]), int(first[5:7])
    out = []
    while f"{y:04d}-{m:02d}" <= last:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def search_terms(month: str) -> dict[str, str]:
    lo, hi = month_range(month)
    base = f"author:{USER} is:public"
    return {
        "prs_opened": f"{base} type:pr created:{lo}..{hi}",
        "prs_merged": f"{base} type:pr is:merged merged:{lo}..{hi}",
        "prs_closed": f"{base} type:pr is:closed -is:merged closed:{lo}..{hi}",
        "issues_opened": f"{base} type:issue created:{lo}..{hi}",
        "issues_closed": f"{base} type:issue closed:{lo}..{hi}",
    }


def fetch_search_counts(gh: GitHub, months: list[str]) -> dict[str, dict[str, int]]:
    jobs = [(m, field, q) for m in months for field, q in search_terms(m).items()]
    out: dict[str, dict[str, int]] = {m: {} for m in months}
    for i in range(0, len(jobs), SEARCH_BATCH):
        chunk = jobs[i : i + SEARCH_BATCH]
        body = "".join(
            f'a{j}: search(query: "{q}", type: ISSUE) {{ issueCount }}\n' for j, (_, _, q) in enumerate(chunk)
        )
        data = gh.query("query {\n" + body + "}")
        for j, (m, field, _) in enumerate(chunk):
            out[m][field] = data[f"a{j}"]["issueCount"]
        log(f"search {min(i + SEARCH_BATCH, len(jobs))}/{len(jobs)}")
    return out


def fetch_contributions(gh: GitHub, months: list[str]) -> dict[str, dict[str, int]]:
    """Reviews and commits per month, public repositories only."""
    out: dict[str, dict[str, int]] = {}
    for i in range(0, len(months), CONTRIB_BATCH):
        chunk = months[i : i + CONTRIB_BATCH]
        body = ""
        for j, m in enumerate(chunk):
            lo, hi = month_range(m)
            body += (
                f'c{j}: contributionsCollection(from: "{lo}T00:00:00Z", to: "{hi}T23:59:59Z") {{\n'
                "  commitContributionsByRepository(maxRepositories: 100) { repository { isPrivate } contributions { totalCount } }\n"
                "  pullRequestReviewContributionsByRepository(maxRepositories: 100) { repository { isPrivate } contributions { totalCount } }\n"
                "}\n"
            )
        data = gh.query(f'query {{ user(login: "{USER}") {{\n{body}}} }}')["user"]
        for j, m in enumerate(chunk):
            c = data[f"c{j}"]
            out[m] = {
                "commits": sum(
                    r["contributions"]["totalCount"]
                    for r in c["commitContributionsByRepository"]
                    if not r["repository"]["isPrivate"]
                ),
                "reviews": sum(
                    r["contributions"]["totalCount"]
                    for r in c["pullRequestReviewContributionsByRepository"]
                    if not r["repository"]["isPrivate"]
                ),
            }
        log(f"contributions {min(i + CONTRIB_BATCH, len(months))}/{len(months)}")
    return out


def fetch_comment_months(gh: GitHub) -> Counter[str]:
    """Public issue and PR conversation comments per month (whole history each run)."""
    counts: Counter[str] = Counter()
    cursor = None
    pages = 0
    while True:
        after = f', after: "{cursor}"' if cursor else ""
        data = gh.query(
            f'query {{ user(login: "{USER}") {{ issueComments(first: 100{after}) {{'
            " pageInfo { hasNextPage endCursor }"
            " nodes { createdAt issue { repository { isPrivate } } } } } }"
        )["user"]["issueComments"]
        for node in data["nodes"]:
            issue = node.get("issue")
            if issue and issue["repository"]["isPrivate"]:
                continue
            counts[node["createdAt"][:7]] += 1
        pages += 1
        if pages % 20 == 0:
            log(f"comments: {pages} pages")
        if not data["pageInfo"]["hasNextPage"]:
            return counts
        cursor = data["pageInfo"]["endCursor"]


def read_rows() -> dict[str, dict[str, str]]:
    if not MONTHLY_CSV.exists():
        return {}
    with MONTHLY_CSV.open(newline="") as f:
        return {r["month"]: r for r in csv.DictReader(f)}


def write_rows(rows: dict[str, dict[str, str]]) -> None:
    DATA.mkdir(exist_ok=True)
    tmp = MONTHLY_CSV.with_suffix(".csv.tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        for month in sorted(rows):
            w.writerow(rows[month])
    tmp.replace(MONTHLY_CSV)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--full", action="store_true", help="recompute every month since the account was created")
    ap.add_argument("--months", type=int, default=3, help="recent months to recompute (default 3)")
    args = ap.parse_args()

    gh = GitHub()
    created = gh.query(f'query {{ user(login: "{USER}") {{ createdAt }} }}')["user"]["createdAt"][:7]
    today = dt.datetime.now(dt.timezone.utc).date()
    this_month = f"{today.year:04d}-{today.month:02d}"
    rows = {} if args.full else read_rows()
    all_months = months_between(created, this_month)
    todo = all_months if args.full or not rows else all_months[-args.months :]
    log(f"recomputing {len(todo)} of {len(all_months)} months")

    search = fetch_search_counts(gh, todo)
    contrib = fetch_contributions(gh, todo)
    comments = fetch_comment_months(gh)

    for month in todo:
        rows[month] = {
            "month": month,
            "issue_comments": str(comments.get(month, 0)),
            **{k: str(v) for k, v in search[month].items()},
            **{k: str(v) for k, v in contrib[month].items()},
        }
    write_rows(rows)
    log(f"wrote {len(rows)} months to {MONTHLY_CSV.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
