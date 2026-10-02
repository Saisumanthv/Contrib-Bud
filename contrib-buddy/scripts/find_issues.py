#!/usr/bin/env python3
"""Find unclaimed, beginner-friendly open issues in a GitHub repo.

Usage:
    python scripts/find_issues.py OWNER/REPO [--limit N] [--claim-days D] [--no-comment-check]

Matches the repo's own beginner labels (good first issue, help wanted,
beginner, easy, hacktoberfest, ...), then drops issues that are assigned,
have a linked open PR, or have a recent "I'll take this" comment.
Prints JSON: {"repo", "labels_used", "issues": [...], "skipped": [...]}.
"""

from __future__ import annotations

import argparse
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from _github import GitHubError, api_get, emit_json, parse_repo, run_cli

# Ordered by how strongly a label signals "good for newcomers".
BEGINNER_LABEL_PATTERNS: tuple[tuple[int, str], ...] = (
    (0, r"good[\s_-]*first[\s_-]*(issue|bug|pr|contribution)?"),
    (0, r"first[\s_-]*timers?([\s_-]*only)?"),
    (1, r"beginner([\s_-]*friendly)?"),
    (1, r"\beasy\b|difficulty:?\s*(easy|low|beginner)|\bstarter\b|low[\s_-]*hanging"),
    (2, r"help[\s_-]*wanted|up[\s_-]*for[\s_-]*grabs|contributions?[\s_-]*welcome"),
    (3, r"hacktoberfest"),
)
EXCLUDED_LABELS = re.compile(r"accepted|invalid|spam|wontfix|won't fix|duplicate|blocked", re.I)

CLAIM_RE = re.compile(
    r"\b(i'?ll|i will|i'?d like to|i would like to|i want to|let me|can i|could i|may i)\s+"
    r"(take|work on|pick( up)?|handle|try|fix|tackle|grab)\b"
    r"|\bassign (this|it|me)\b|\bassign\b.{0,20}\bto me\b|\bi'?m (currently )?working on\b"
    r"|\bi am (currently )?working on\b|\bworking on (this|it)\b|^/assign\b",
    re.IGNORECASE | re.MULTILINE,
)
DEFAULT_LABELS = ("good first issue", "help wanted")
SEARCH_QUERY_MAX = 250


def label_rank(name: str) -> int | None:
    """Return how strongly a label signals beginner-friendliness (0 best), or None."""
    if EXCLUDED_LABELS.search(name):
        return None
    for rank, pattern in BEGINNER_LABEL_PATTERNS:
        if re.search(pattern, name, re.IGNORECASE):
            return rank
    return None


def match_beginner_labels(names: list[str]) -> list[str]:
    """Return repo label names that signal beginner-friendliness, best first."""
    ranked = [(r, n) for n in names if (r := label_rank(n)) is not None]
    return [n for _, n in sorted(ranked, key=lambda r: (r[0], r[1].lower()))]


def build_search_query(owner: str, repo: str, labels: list[str], linked_filter: bool) -> str:
    """Build a GitHub issue-search query, keeping it under the length limit."""
    base = f"repo:{owner}/{repo} is:issue is:open no:assignee"
    if linked_filter:
        base += " -linked:pr"
    chosen: list[str] = []
    for label in labels:
        candidate = chosen + [label]
        q = base + " label:" + ",".join(f'"{x}"' for x in candidate)
        if len(q) > SEARCH_QUERY_MAX:
            break
        chosen = candidate
    return base + " label:" + ",".join(f'"{x}"' for x in chosen)


def find_claim(
    comments: list[dict[str, Any]], now: datetime, days: int = 14
) -> dict[str, Any] | None:
    """Return the most recent claim-like comment within `days`, or None.

    Bot comments are ignored. Each comment needs 'body', 'created_at', 'user'.
    """
    cutoff = now - timedelta(days=days)
    for c in sorted(comments, key=lambda c: c.get("created_at", ""), reverse=True):
        user = c.get("user") or {}
        if user.get("type") == "Bot" or str(user.get("login", "")).endswith("[bot]"):
            continue
        created = _parse_time(c.get("created_at"))
        if created is None or created < cutoff:
            continue
        m = CLAIM_RE.search(c.get("body") or "")
        if m:
            return {
                "by": user.get("login"),
                "at": c.get("created_at"),
                "text": (c.get("body") or "").strip().splitlines()[0][:160],
            }
    return None


def filter_issues(
    issues: list[dict[str, Any]], claims: dict[int, dict[str, Any] | None] | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split raw GitHub issue dicts into (available, skipped-with-reason).

    Skips pull requests, assigned issues, issues flagged with a linked PR
    (`has_linked_pr`), and issues with a recent claim in `claims`.
    """
    claims = claims or {}
    kept, skipped = [], []
    for issue in issues:
        reason = None
        if "pull_request" in issue:
            reason = "is a pull request"
        elif issue.get("assignee") or issue.get("assignees"):
            who = (issue.get("assignee") or issue["assignees"][0]).get("login", "someone")
            reason = f"assigned to @{who}"
        elif issue.get("has_linked_pr"):
            reason = "has a linked pull request"
        elif claims.get(issue["number"]):
            c = claims[issue["number"]]
            reason = f"claimed by @{c['by']} on {str(c['at'])[:10]}: \"{c['text']}\""
        if reason:
            skipped.append({"number": issue["number"], "title": issue.get("title"),
                            "reason": reason})
        else:
            kept.append(issue)
    return kept, skipped


def summarize_issue(issue: dict[str, Any], now: datetime, body_chars: int = 3000) -> dict[str, Any]:
    """Reduce a GitHub issue to the fields downstream tools need."""
    created = _parse_time(issue.get("created_at")) or now
    updated = _parse_time(issue.get("updated_at")) or created
    body = issue.get("body") or ""
    return {
        "number": issue["number"],
        "title": issue.get("title", ""),
        "url": issue.get("html_url", ""),
        "labels": [lbl["name"] if isinstance(lbl, dict) else lbl
                   for lbl in issue.get("labels", [])],
        "age_days": (now - created).days,
        "days_since_update": (now - updated).days,
        "comments": issue.get("comments", 0),
        "reactions": (issue.get("reactions") or {}).get("total_count", 0),
        "author": (issue.get("user") or {}).get("login"),
        "created_at": issue.get("created_at"),
        "body": body[:body_chars] + ("\n…[truncated]" if len(body) > body_chars else ""),
    }


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def fetch_labels(owner: str, repo: str) -> list[str]:
    """Return all label names in the repo (up to 300)."""
    names: list[str] = []
    for page in (1, 2, 3):
        batch = api_get(
            f"/repos/{owner}/{repo}/labels", {"per_page": 100, "page": page},
            not_found=f"Repository {owner}/{repo} was not found (or is private).",
        )
        names += [b["name"] for b in batch]
        if len(batch) < 100:
            break
    return names


def search_issues(owner: str, repo: str, labels: list[str], per_page: int) -> tuple[list, str]:
    """Search open, unassigned issues with any of `labels` and no linked PR."""
    query = build_search_query(owner, repo, labels, linked_filter=True)
    params = {"q": query, "sort": "updated", "order": "desc", "per_page": per_page}
    try:
        data = api_get("/search/issues", params)
    except GitHubError as exc:
        if "422" not in str(exc):
            raise
        query = build_search_query(owner, repo, labels, linked_filter=False)
        data = api_get("/search/issues", {**params, "q": query})
    return data.get("items", []), query


def find_issues(
    repo_arg: str, limit: int = 10, claim_days: int = 14, check_comments: bool = True
) -> dict[str, Any]:
    """Find available beginner-friendly issues. See module docstring."""
    owner, repo = parse_repo(repo_arg)
    now = datetime.now(UTC)
    all_labels = fetch_labels(owner, repo)
    labels = match_beginner_labels(all_labels)
    note = None
    if not labels:
        labels = list(DEFAULT_LABELS)
        note = "No beginner-style labels found in this repo; searched the default labels."

    # Tier 1: explicit newcomer labels. Tier 2 (help wanted, hacktoberfest) only if needed.
    per_page = min(100, limit * 3)
    tier1 = [n for n in labels if (label_rank(n) or 0) <= 1]
    tier2 = [n for n in labels if n not in tier1]
    items, queries = [], []
    for tier in (tier1, tier2):
        if not tier or len(items) >= limit * 2:  # enough headroom for claim filtering
            continue
        found, q = search_issues(owner, repo, tier, per_page)
        seen = {i["number"] for i in items}
        items += [i for i in found if i["number"] not in seen]
        queries.append(q)
    query = " | ".join(queries)
    candidates, skipped = filter_issues(items)

    claims: dict[int, dict[str, Any] | None] = {}
    available: list[dict[str, Any]] = []
    since = (now - timedelta(days=claim_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for issue in candidates:
        if len(available) >= limit:
            break
        if check_comments and issue.get("comments", 0) > 0:
            comments = api_get(f"/repos/{owner}/{repo}/issues/{issue['number']}/comments",
                               {"since": since, "per_page": 100})
            claims[issue["number"]] = find_claim(comments, now, claim_days)
        kept, dropped = filter_issues([issue], claims)
        available += kept
        skipped += dropped

    return {
        "repo": f"{owner}/{repo}",
        "query": query,
        "labels_used": labels,
        "note": note,
        "comment_check": check_comments,
        "issues": [summarize_issue(i, now) for i in available],
        "skipped": skipped,
    }


def main() -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("repo", help="owner/repo or GitHub URL")
    ap.add_argument("--limit", type=int, default=10, help="max issues to return (default 10)")
    ap.add_argument("--claim-days", type=int, default=14,
                    help="treat 'I'll take this' comments newer than this as claims (default 14)")
    ap.add_argument("--no-comment-check", action="store_true",
                    help="skip per-issue comment checks (saves API calls)")
    args = ap.parse_args()
    if args.limit < 1:
        raise GitHubError("--limit must be at least 1")
    emit_json(find_issues(args.repo, args.limit, args.claim_days, not args.no_comment_check))
    return 0


if __name__ == "__main__":
    run_cli(main)
