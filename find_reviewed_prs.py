#!/usr/bin/env python3
"""
find_reviewed_prs.py

Query NixOS/nixpkgs for merged PRs that have at least one review, with
optional filtering by label, author, date range, and minimum review count.

Outputs a plain list of PR numbers (one per line) that can be fed directly
into scrape_nixpkgs_prs.py --pr-file.

Usage examples:

  # 500 recently merged PRs that received at least one review
  python find_reviewed_prs.py --limit 500

  # Only package additions / version bumps
  python find_reviewed_prs.py --labels "6.topic: packaging" --limit 200

  # Merged in 2024, at least 2 reviews, save to a file
  python find_reviewed_prs.py --since 2024-01-01 --until 2024-12-31 \\
      --min-reviews 2 --out training_prs.txt

  # Then scrape them
  python scrape_nixpkgs_prs.py --pr-file training_prs.txt

Environment variables:
    GITHUB_TOKEN   Strongly recommended (5 000 req/hr vs 60 unauthed).
                   The script uses both Search API and REST; search results
                   cap at 1 000 hits per query, so large batches are split
                   automatically by date window.
"""

import argparse
import os
import sys
import time
from datetime import date, timedelta, datetime
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("requests is not installed.  Run: pip install requests")


REPO = "NixOS/nixpkgs"
BASE_URL = "https://api.github.com"
HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
TOKEN = os.environ.get("GITHUB_TOKEN", "")
if TOKEN:
    HEADERS["Authorization"] = f"Bearer {TOKEN}"


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _get(url: str, params: dict = None) -> dict | list:
    while True:
        r = requests.get(url, headers=HEADERS, params=params, timeout=30)
        if r.status_code == 403 and "rate limit" in r.text.lower():
            reset = int(r.headers.get("X-RateLimit-Reset", time.time() + 60))
            wait = max(reset - time.time(), 1) + 2
            print(f"  [rate limit] sleeping {wait:.0f}s …", flush=True)
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()


def search_prs(query: str, limit: int) -> list[dict]:
    """
    Use the GitHub Search API to find PRs matching `query`.
    Handles pagination and the 1 000-result cap transparently — callers
    that need more should split by date window (see _iter_windows).
    """
    url = f"{BASE_URL}/search/issues"
    results = []
    page = 1
    per_page = min(100, limit)

    while len(results) < limit:
        params = {"q": query, "per_page": per_page, "page": page, "sort": "updated", "order": "desc"}
        data = _get(url, params)
        items = data.get("items", [])
        if not items:
            break
        results.extend(items)
        # Search API caps at 1 000 results total
        if len(results) >= min(limit, 1000, data.get("total_count", 0)):
            break
        page += 1
        # Search API secondary rate limit: max ~30 req/min
        time.sleep(2)

    return results[:limit]


def get_review_count(pr_number: int) -> int:
    """Return the number of reviews submitted for a PR."""
    url = f"{BASE_URL}/repos/{REPO}/pulls/{pr_number}/reviews"
    try:
        reviews = _get(url, {"per_page": 100})
        # reviews is a list; might be paginated for very active PRs but
        # we only need to know "≥ min_reviews", so count what we get
        return len(reviews) if isinstance(reviews, list) else 0
    except requests.HTTPError:
        return 0


# ---------------------------------------------------------------------------
# Date-window splitting to bypass the 1 000-result Search API cap
# ---------------------------------------------------------------------------

def _iter_windows(since: date, until: date, window_days: int = 30):
    """Yield (start, end) date pairs of `window_days` length."""
    cursor = since
    while cursor <= until:
        end = min(cursor + timedelta(days=window_days - 1), until)
        yield cursor, end
        cursor = end + timedelta(days=1)


def build_query(since_str: str | None, until_str: str | None, labels: list[str], keywords: list[str]) -> str:
    q = f"repo:{REPO} is:pr is:merged reviewed:true"
    for label in labels:
        q += f' label:"{label}"'
    for kw in keywords:
        # Quoted so multi-word phrases like "final attrs" work too
        q += f' "{kw}"'
    return q  # date range appended per-window in the loop below


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def collect_pr_numbers(
    limit: int,
    min_reviews: int,
    since: date | None,
    until: date | None,
    labels: list[str],
    keywords: list[str],
    out_path: Path,
) -> None:
    if not TOKEN:
        print(
            "WARNING: GITHUB_TOKEN not set.  You will hit the 60 req/hr "
            "unauthenticated limit very quickly.\n"
        )

    since = since or date(2023, 1, 1)
    until = until or date.today()

    base_query = build_query(None, None, labels, keywords)
    collected: list[int] = []
    seen: set[int] = set()

    print(f"Searching merged+reviewed PRs in {REPO}")
    print(f"Date range : {since} → {until}")
    print(f"Min reviews: {min_reviews}")
    print(f"Labels     : {labels or '(any)'}")
    print(f"Keywords   : {keywords or '(any)'}")
    print(f"Target     : {limit} PRs\n")

    windows = list(_iter_windows(since, until, window_days=30))
    # Reverse so we get the most recent first (better training signal)
    windows.reverse()

    for w_start, w_end in windows:
        if len(collected) >= limit:
            break

        window_query = (
            f"{base_query} merged:{w_start.isoformat()}..{w_end.isoformat()}"
        )
        print(f"  window {w_start} → {w_end}  (collected {len(collected)}/{limit})", flush=True)

        try:
            items = search_prs(window_query, limit=min(1000, limit * 3))
        except requests.HTTPError as e:
            print(f"  [search error] {e} — skipping window")
            time.sleep(5)
            continue

        for item in items:
            if len(collected) >= limit:
                break

            pr_number = item["number"]
            if pr_number in seen:
                continue
            seen.add(pr_number)

            # Fast path: if min_reviews == 1 the search query already
            # guarantees at least one review, no extra API call needed.
            if min_reviews <= 1:
                collected.append(pr_number)
                continue

            # Otherwise verify review count (costs 1 API call per PR)
            count = get_review_count(pr_number)
            if count >= min_reviews:
                collected.append(pr_number)
                print(f"    ✓ #{pr_number}  ({count} reviews)")
            else:
                print(f"    ✗ #{pr_number}  ({count} reviews, need {min_reviews})")

            time.sleep(0.3)  # polite pacing

    # Write results
    if collected:
        out_path.write_text("\n".join(str(n) for n in collected) + "\n", encoding="utf-8")
    else:
        out_path.write_text("", encoding="utf-8")
    print(f"\nWrote {len(collected)} PR numbers → {out_path}")

    if len(collected) < limit:
        print(
            f"Note: only found {len(collected)} PRs matching the criteria "
            f"(wanted {limit}).  Widen the date range or relax filters."
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def valid_date(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid date '{s}', expected YYYY-MM-DD")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find merged NixOS/nixpkgs PRs with reviews and output PR numbers."
    )
    parser.add_argument(
        "--limit", type=int, default=200,
        help="How many PRs to collect (default: 200)",
    )
    parser.add_argument(
        "--min-reviews", type=int, default=1,
        help="Minimum number of reviews a PR must have (default: 1)",
    )
    parser.add_argument(
        "--since", type=valid_date, default=None,
        help="Only include PRs merged on or after this date (YYYY-MM-DD, default: 2023-01-01)",
    )
    parser.add_argument(
        "--until", type=valid_date, default=None,
        help="Only include PRs merged on or before this date (YYYY-MM-DD, default: today)",
    )
    parser.add_argument(
        "--labels", type=str, default="",
        help=(
            "Comma-separated list of GitHub labels to filter by.  "
            "Common nixpkgs labels: '6.topic: packaging', '8.has: changelog', "
            "'merge bot'. Example: --labels '6.topic: packaging'"
        ),
    )
    parser.add_argument(
        "--keywords", type=str, default="",
        help=(
            "Comma-separated keywords to search for in PR title/body.  "
            "Each keyword is matched as a phrase (quoted).  "
            "Example: --keywords 'treewide,finalAttrs,passthru.tests'"
        ),
    )
    parser.add_argument(
        "--out", type=Path, default=Path("reviewed_prs.txt"),
        help="Output file path (default: reviewed_prs.txt)",
    )
    args = parser.parse_args()

    labels = [l.strip() for l in args.labels.split(",") if l.strip()]
    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]

    collect_pr_numbers(
        limit=args.limit,
        min_reviews=args.min_reviews,
        since=args.since,
        until=args.until,
        labels=labels,
        keywords=keywords,
        out_path=args.out,
    )

    print(f"\nNext step:\n  python scrape_nixpkgs_prs.py --pr-file {args.out} --out ./pr_data")


if __name__ == "__main__":
    main()
