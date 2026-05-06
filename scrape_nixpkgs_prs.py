#!/usr/bin/env python3
"""
scrape_nixpkgs_prs.py

Scrape NixOS/nixpkgs pull requests (comments + reviews + diff) into plain
text files suitable for fine-tuning a review agent.

Usage:
    # Single PR
    python scrape_nixpkgs_prs.py --pr 12345

    # Range of PRs
    python scrape_nixpkgs_prs.py --pr-range 300000 300100

    # From a file with one PR number per line
    python scrape_nixpkgs_prs.py --pr-file pr_numbers.txt

    # Output directory (default: ./pr_data)
    python scrape_nixpkgs_prs.py --pr 12345 --out ./training_data

Environment variables:
    GITHUB_TOKEN   Personal access token (strongly recommended to avoid
                   rate-limiting; needs no special scopes for public repos)
"""

import argparse
import os
import sys
import time
import json
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("requests is not installed. Run: pip install requests")


REPO = "NixOS/nixpkgs"
BASE_URL = "https://api.github.com"
HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

# Patch the token in at runtime so the constant stays clean
TOKEN = os.environ.get("GITHUB_TOKEN", "")
if TOKEN:
    HEADERS["Authorization"] = f"Bearer {TOKEN}"


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------

def _get(url: str, params: dict = None) -> dict | list:
    """GET with automatic rate-limit retry and pagination support."""
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


def paginate(url: str, params: dict = None) -> list:
    """Collect all pages of a GitHub list endpoint."""
    params = dict(params or {})
    params.setdefault("per_page", 100)
    results = []
    page = 1
    while True:
        params["page"] = page
        chunk = _get(url, params)
        if not chunk:
            break
        results.extend(chunk)
        if len(chunk) < params["per_page"]:
            break
        page += 1
    return results


def get_diff(pr_number: int) -> str:
    """Fetch the raw unified diff for a PR."""
    url = f"{BASE_URL}/repos/{REPO}/pulls/{pr_number}"
    r = requests.get(
        url,
        headers={**HEADERS, "Accept": "application/vnd.github.diff"},
        timeout=60,
    )
    r.raise_for_status()
    return r.text


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_pr(pr_number: int) -> dict:
    url = f"{BASE_URL}/repos/{REPO}/pulls/{pr_number}"
    return _get(url)


def fetch_issue_comments(pr_number: int) -> list:
    """Top-level conversation comments (not inline review comments)."""
    url = f"{BASE_URL}/repos/{REPO}/issues/{pr_number}/comments"
    return paginate(url)


def fetch_reviews(pr_number: int) -> list:
    """Review objects (APPROVED / CHANGES_REQUESTED / COMMENTED + body)."""
    url = f"{BASE_URL}/repos/{REPO}/pulls/{pr_number}/reviews"
    return paginate(url)


def fetch_review_comments(pr_number: int) -> list:
    """Inline diff comments attached to a review."""
    url = f"{BASE_URL}/repos/{REPO}/pulls/{pr_number}/comments"
    return paginate(url)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _author(obj: dict) -> str:
    user = obj.get("user") or {}
    return user.get("login", "unknown")


def format_pr(pr_number: int) -> str:
    print(f"  fetching PR metadata …", flush=True)
    pr = fetch_pr(pr_number)

    print(f"  fetching diff …", flush=True)
    diff = get_diff(pr_number)

    print(f"  fetching issue comments …", flush=True)
    issue_comments = fetch_issue_comments(pr_number)

    print(f"  fetching reviews …", flush=True)
    reviews = fetch_reviews(pr_number)

    print(f"  fetching inline review comments …", flush=True)
    review_comments = fetch_review_comments(pr_number)

    # Index inline comments by review id for easy lookup
    comments_by_review: dict[int, list] = {}
    for c in review_comments:
        rid = c.get("pull_request_review_id")
        comments_by_review.setdefault(rid, []).append(c)

    lines: list[str] = []

    # ── Header ──────────────────────────────────────────────────────────────
    lines += [
        "=" * 72,
        f"PR #{pr_number}: {pr.get('title', '')}",
        f"Author : {_author(pr)}",
        f"State  : {pr.get('state', '')}  |  Merged: {pr.get('merged', False)}",
        f"URL    : {pr.get('html_url', '')}",
        f"Base   : {pr.get('base', {}).get('ref', '')}  ←  "
        f"{pr.get('head', {}).get('ref', '')}",
        "=" * 72,
    ]

    # ── PR body ──────────────────────────────────────────────────────────────
    body = (pr.get("body") or "").strip()
    if body:
        lines += ["", "## PR DESCRIPTION", "", body, ""]

    # ── Labels / tags ────────────────────────────────────────────────────────
    labels = [lbl["name"] for lbl in pr.get("labels", [])]
    if labels:
        lines += [f"Labels: {', '.join(labels)}", ""]

    # ── Diff ─────────────────────────────────────────────────────────────────
    lines += ["", "## DIFF", ""]
    lines.append(diff)

    # ── Conversation comments ─────────────────────────────────────────────
    if issue_comments:
        lines += ["", "## CONVERSATION COMMENTS", ""]
        for c in issue_comments:
            lines += [
                f"--- comment by @{_author(c)} at {c.get('created_at', '')} ---",
                (c.get("body") or "").strip(),
                "",
            ]

    # ── Reviews (with their inline comments interleaved) ──────────────────
    if reviews:
        lines += ["", "## REVIEWS", ""]
        for review in reviews:
            rid = review.get("id")
            state = review.get("state", "COMMENTED")
            reviewer = _author(review)
            submitted_at = review.get("submitted_at", "")
            review_body = (review.get("body") or "").strip()

            lines += [
                f"--- review by @{reviewer} [{state}] at {submitted_at} ---",
            ]
            if review_body:
                lines += [review_body, ""]

            # Inline comments belonging to this review
            inlines = comments_by_review.get(rid, [])
            if inlines:
                lines.append("  Inline comments:")
                for ic in inlines:
                    path = ic.get("path", "")
                    line_info = ""
                    if ic.get("line"):
                        line_info = f" line {ic['line']}"
                    elif ic.get("original_line"):
                        line_info = f" line {ic['original_line']}"

                    diff_hunk = (ic.get("diff_hunk") or "").strip()
                    comment_body = (ic.get("body") or "").strip()
                    in_reply_to = ic.get("in_reply_to_id")

                    lines.append(
                        f"  [{path}{line_info}]"
                        + (f" (reply to #{in_reply_to})" if in_reply_to else "")
                    )
                    if diff_hunk:
                        for dl in diff_hunk.splitlines():
                            lines.append(f"    > {dl}")
                    lines += [f"  @{_author(ic)}: {comment_body}", ""]

    lines.append("=" * 72)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def process_pr(pr_number: int, out_dir: Path) -> None:
    out_path = out_dir / f"pr_{pr_number}.txt"
    if out_path.exists():
        print(f"[skip] #{pr_number} already exists at {out_path}")
        return

    print(f"[PR #{pr_number}]")
    try:
        text = format_pr(pr_number)
        out_path.write_text(text, encoding="utf-8")
        print(f"  → written to {out_path}")
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            print(f"  [404] PR #{pr_number} not found, skipping.")
        else:
            print(f"  [error] {e}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape NixOS/nixpkgs PR reviews into plain text."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pr", type=int, help="Single PR number")
    group.add_argument(
        "--pr-range",
        nargs=2,
        type=int,
        metavar=("START", "END"),
        help="Inclusive range of PR numbers",
    )
    group.add_argument(
        "--pr-file",
        type=Path,
        help="File with one PR number per line",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("./pr_data"),
        help="Output directory (default: ./pr_data)",
    )
    args = parser.parse_args()

    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.pr:
        numbers = [args.pr]
    elif args.pr_range:
        numbers = list(range(args.pr_range[0], args.pr_range[1] + 1))
    else:
        numbers = [
            int(line.strip())
            for line in args.pr_file.read_text().splitlines()
            if line.strip().isdigit()
        ]

    print(f"Processing {len(numbers)} PR(s) → {out_dir}")
    if not TOKEN:
        print(
            "WARNING: GITHUB_TOKEN not set. You will hit rate limits quickly "
            "(60 req/hr unauthenticated vs 5000/hr authenticated)."
        )

    for pr_number in numbers:
        process_pr(pr_number, out_dir)
        # small polite delay between PRs
        time.sleep(0.5)

    print("Done.")


if __name__ == "__main__":
    main()
