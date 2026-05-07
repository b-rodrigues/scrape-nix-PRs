# scrape-nix-PRs

Tools for scraping NixOS/nixpkgs pull requests (metadata, diffs, and reviews) to create training data for AI review agents.

## Usage with Nix Flakes (Recommended)

This repository provides a Nix flake for easy execution without manually managing Python dependencies.

### 1. Find PRs of interest

Use `find-prs` to query GitHub for merged PRs that have reviews.

```bash
# Find 100 PRs with "treewide" or "pythonPackages" in the title/body
nix run .#find-prs -- --limit 100 --keywords "treewide,pythonPackages" --or-keywords --out pr_list.txt
```

**Options for `find-prs`:**
- `--limit`: Number of PRs to find (default: 200).
- `--keywords`: Comma-separated list of keywords.
- `--or-keywords`: If provided, matches *any* keyword (OR logic). Otherwise matches *all* (AND logic).
- `SINCE`: Start date (YYYY-MM-DD, e.g., `2024-01-01`).
- `MIN_REVIEWS`: Minimum number of reviews (default: 1).
- `LABELS`: Comma-separated labels (e.g., `6.topic: packaging`).
- `LIMIT_CFG`: Number of PRs to find for this specific config (overrides global limit).

### 2. Scrape the data

Use `scrape-prs` to fetch the full content of the PRs identified in the first step.

```bash
# Scrape the PRs listed in pr_list.txt
nix run .#scrape-prs -- --pr-file pr_list.txt --out ./my_data
```

**Options for `scrape-prs`:**
- `--pr`: Scrape a single PR number.
- `--pr-file`: File with one PR number per line.
- `--pr-range`: Inclusive range of PR numbers (e.g., `300000 300100`).
- `--out`: Output directory (default: `./pr_data`).

## Environment Variables

- `GITHUB_TOKEN`: **Strongly Recommended.** Without a token, you are limited to 60 requests per hour, and the scripts will frequently pause to wait for rate limits to reset. With a token, you get 5,000 requests per hour.

## Development Shell

If you want to run the scripts directly or modify them:

```bash
nix develop
python3 find_reviewed_prs.py --help
```

## Automating with GitHub Actions

The repository includes a workflow in `.github/workflows/scrape_nixpkgs_prs.yml` that can be configured via `.env` files in the `configs/` directory to automatically scrape new PRs on a schedule.
