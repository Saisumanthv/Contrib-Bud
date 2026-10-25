#!/usr/bin/env python3
"""Learn from similar issues that were already fixed by a merged pull request.

Usage:
    python scripts/similar_prs.py https://github.com/OWNER/REPO/issues/123

Searches the repo's closed issues that have a linked PR (the top keywords from the
issue title, then each alone, then its labels; without the linked-PR filter if
that finds too little), re-ranks the results by how many keywords and labels they
share with the issue, and for the best matches finds the merged PR that
fixed it (from the issue timeline) and reports the files that PR touched, how
big it was, how long it took to merge and how reviewers responded. Files that
several of those PRs touched are the best evidence of where a fix belongs.
Prints JSON: {"repo", "queries", "examples": [...], "history_files": [...]}.
Uses 2-8 searches plus 3-5 API calls per candidate issue (up to 6).
"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from _github import GitHubError, api_get, emit_json, parse_issue_url, run_cli

EXAMPLES = 3          # merged PRs to report
CANDIDATES = 6        # similar issues whose timelines are read to find those PRs
SEARCH_PAGE = 20      # results per search, re-ranked locally
LARGE_PR_FILES = 30   # bigger PRs are poor models for a first PR and swamp the file history
MAX_KEYWORDS = 4
MAX_FILES_SHOWN = 12  # files listed per PR
NOISE_FILES = re.compile(
    r"(lock\.json|\.lock|-lock\.yaml|\.min\.js|\.map|\.snap)$|(^|/)(dist|vendor|node_modules)/"
)
# Labels that say nothing about *where* a change goes.
GENERIC_LABELS = re.compile(
    r"good[\s_-]*first|first[\s_-]*timer|beginner|easy|help[\s_-]*wanted|hacktoberfest|"
    r"up[\s_-]*for[\s_-]*grabs|triage|needs?[\s_-]|status:|priority|p[0-4]\b|stale",
    re.IGNORECASE,
)
MAINTAINER_ROLES = {"OWNER", "MEMBER", "COLLABORATOR"}
URLISH = re.compile(r"^(https?:|www\.)|\.(com|org|net|io|dev|html?)(/|$)", re.IGNORECASE)


# --- pure helpers -------------------------------------------------------------------

def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _term(word: str) -> str:
    """Quote a search term that contains anything but letters and digits."""
    return f'"{word}"' if re.search(r"[^\w]", word) else word


def specific_labels(labels: list[str]) -> list[str]:
    return [lbl for lbl in labels if not GENERIC_LABELS.search(lbl)]


def build_queries(owner: str, repo: str, keywords: list[str], labels: list[str],
                  linked: bool = True) -> list[str]:
    """Search queries for closed issues, most precise first: the top two keywords
    together, each of them in a title, then the issue's specific labels. (Short queries:
    GitHub search finds little with many AND terms and ranks long OR lists poorly.)"""
    base = f"repo:{owner}/{repo} is:issue is:closed" + (" linked:pr" if linked else "")
    terms = [_term(k) for k in keywords[:2]]
    queries = []
    if len(terms) == 2:
        queries.append(f"{base} {terms[0]} {terms[1]}")
    queries += [f"{base} {t} in:title" for t in terms]
    if specific := specific_labels(labels):
        queries.append(f"{base} " + " ".join(f'label:"{lbl}"' for lbl in specific[:2]))
    return queries


def similarity(keywords: list[str], labels: list[str], found: dict[str, Any]) -> float:
    """How alike a found issue is: keywords in its title count most (earlier keywords
    weigh more), then in its body, then shared specific labels. 0 unless the title
    or a label matches: body-only matches are mostly noise."""
    title = (found.get("title") or "").lower()
    body = (found.get("body") or "").lower()
    title_score = body_score = 0.0
    for i, word in enumerate(keywords):
        weight = len(keywords) - i
        if word.lower() in title:
            title_score += 2 * weight
        elif word.lower() in body:
            body_score += 0.5 * weight
    found_labels = {lbl["name"] for lbl in found.get("labels", [])}
    label_score = 3 * len(set(specific_labels(labels)) & found_labels)
    if not title_score and not label_score:
        return 0.0
    return title_score + body_score + label_score


def _pr_info(pr: dict[str, Any], merged_at: str) -> dict[str, Any]:
    """The PR fields we use, from either an issue-shaped or a pull-shaped object."""
    return {"number": pr["number"], "title": pr.get("title"), "url": pr.get("html_url"),
            "user": pr.get("user"), "created_at": pr.get("created_at"), "merged_at": merged_at,
            "author_association": pr.get("author_association"), "comments": pr.get("comments")}


def merged_pr_from_timeline(timeline: list[dict[str, Any]],
                            repo_full: str) -> tuple[dict[str, Any] | None, list[str]]:
    """(the first merged same-repo PR that cross-referenced the issue, or None;
    commits that closed or referenced it, newest first, to look up when there is no PR).

    Issues closed by a commit ("Fixes #12" in a squash-merged PR) often have no
    cross-reference event, only the commit.
    """
    closed: list[str] = []
    referenced: list[str] = []
    for event in timeline:
        kind = event.get("event")
        if kind == "closed" and event.get("commit_id"):
            closed.append(event["commit_id"])
        elif kind == "referenced" and event.get("commit_id"):
            referenced.append(event["commit_id"])
        elif kind == "cross-referenced":
            source = (event.get("source") or {}).get("issue") or {}
            merged = (source.get("pull_request") or {}).get("merged_at")
            repo = (source.get("repository") or {}).get("full_name", "").lower()
            if merged and repo == repo_full:
                return _pr_info(source, merged), []
    return None, closed[::-1] + referenced[::-1]


def merged_pr_from_commit(pulls: list[dict[str, Any]], repo_full: str) -> dict[str, Any] | None:
    """The merged PR (in this repo) that a commit belongs to."""
    for pr in pulls:
        base = ((pr.get("base") or {}).get("repo") or {}).get("full_name", "").lower()
        if pr.get("merged_at") and base == repo_full:
            return _pr_info(pr, pr["merged_at"])
    return None


def is_bot(user: dict[str, Any] | None) -> bool:
    user = user or {}
    login = str(user.get("login", ""))
    return user.get("type") == "Bot" or login.endswith(("[bot]", "-bot"))


def summarize_reviews(reviews: list[dict[str, Any]], author: str | None) -> dict[str, Any]:
    """Count review rounds and what reviewers asked for (ignoring the author and bots)."""
    others = [r for r in reviews if (r.get("user") or {}).get("login") not in (author, None)
              and not is_bot(r.get("user"))]
    states = Counter(r.get("state") for r in others)
    reviewers = sorted({r["user"]["login"] for r in others})
    return {"count": len(others), "approvals": states.get("APPROVED", 0),
            "changes_requested": states.get("CHANGES_REQUESTED", 0),
            "comments": states.get("COMMENTED", 0), "reviewers": reviewers}


def review_story(pr: dict[str, Any]) -> str:
    """One plain sentence on how the PR went through review."""
    r = pr["reviews"]
    parts = [f"merged in {pr['days_to_merge']:g} days" if pr["days_to_merge"] is not None
             else "merged"]
    if r["count"]:
        who = ", ".join(f"@{name}" for name in r["reviewers"][:3])
        parts.append(f"after {r['count']} review(s) from {who}")
    else:
        parts.append("with no formal review")
    if r["changes_requested"]:
        parts.append(f"including {r['changes_requested']} request(s) for changes")
    return " ".join(parts)


def history_files(examples: list[dict[str, Any]], paths: set[str] | None = None,
                  limit: int = 8) -> list[dict[str, Any]]:
    """Files touched by the example PRs, most often first (only ones that still exist;
    large PRs are left out)."""
    seen: dict[str, list[int]] = {}
    for ex in examples:
        if ex["pr"]["large"]:
            continue
        for f in ex["pr"]["files"]:
            if NOISE_FILES.search(f["path"]) or (paths is not None and f["path"] not in paths):
                continue
            seen.setdefault(f["path"], []).append(ex["pr"]["number"])
    ranked = sorted(seen.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    return [{"path": p, "prs": prs} for p, prs in ranked[:limit]]


def match_reason(keywords: list[str], labels: list[str], found: dict[str, Any]) -> str:
    title = found.get("title", "").lower()
    shared_words = [k for k in keywords if k.lower() in title]
    shared_labels = sorted(set(labels) & {lbl["name"] for lbl in found.get("labels", [])})
    bits = []
    if shared_words:
        bits.append("title mentions " + ", ".join(shared_words[:3]))
    if shared_labels:
        bits.append("same label " + ", ".join(shared_labels[:2]))
    return "; ".join(bits)


# --- GitHub fetching ------------------------------------------------------------------

def _search(query: str) -> list[dict[str, Any]]:
    try:
        return api_get("/search/issues", {"q": query, "per_page": SEARCH_PAGE}).get("items", [])
    except GitHubError as exc:
        if "422" in str(exc):  # e.g. an unsupported term: skip this query, keep the others
            return []
        raise


def _example(owner: str, repo: str, found: dict[str, Any], keywords: list[str],
             labels: list[str]) -> dict[str, Any] | None:
    timeline = api_get(f"/repos/{owner}/{repo}/issues/{found['number']}/timeline",
                       {"per_page": 100})
    repo_full = f"{owner}/{repo}".lower()
    source, shas = merged_pr_from_timeline(timeline, repo_full)
    for sha in shas[:2]:
        if source:
            break
        try:
            source = merged_pr_from_commit(
                api_get(f"/repos/{owner}/{repo}/commits/{sha}/pulls"), repo_full)
        except GitHubError:  # e.g. a commit from a fork that no longer exists
            continue
    if not source:
        return None
    number = source["number"]
    files = api_get(f"/repos/{owner}/{repo}/pulls/{number}/files", {"per_page": 100})
    reviews = api_get(f"/repos/{owner}/{repo}/pulls/{number}/reviews", {"per_page": 100})
    author = (source.get("user") or {}).get("login")
    created = _parse_time(source.get("created_at"))
    merged = _parse_time(source["merged_at"])
    pr = {
        "number": number, "title": source.get("title"), "url": source.get("url"),
        "author": author,
        "by_maintainer": source.get("author_association") in MAINTAINER_ROLES,
        "merged_at": source["merged_at"],
        "days_to_merge": round((merged - created).total_seconds() / 86400, 1)
        if created and merged else None,
        "additions": sum(f.get("additions", 0) for f in files),
        "deletions": sum(f.get("deletions", 0) for f in files),
        "changed_files": len(files),
        "large": len(files) > LARGE_PR_FILES,
        "files": [{"path": f["filename"], "status": f.get("status"),
                   "additions": f.get("additions", 0), "deletions": f.get("deletions", 0)}
                  for f in files],
        "discussion_comments": source.get("comments"),
        "reviews": summarize_reviews(reviews, author),
    }
    pr["review_story"] = review_story(pr)
    return {
        "issue": {"number": found["number"], "title": found.get("title"),
                  "url": found.get("html_url"),
                  "labels": [lbl["name"] for lbl in found.get("labels", [])]},
        "why": match_reason(keywords, labels, found),
        "pr": pr,
    }


def find_similar_prs(
    owner: str, repo: str, number: int, keywords: list[str], labels: list[str],
    paths: set[str] | None = None, limit: int = EXAMPLES,
) -> dict[str, Any]:
    """Merged PRs that fixed issues like this one. See module docstring.

    `keywords` should be the issue's most specific words, best first; `paths` (the
    current file tree) drops files that no longer exist from `history_files`.
    """
    found: dict[int, dict[str, Any]] = {}
    queries: list[str] = []
    for linked in (True, False):  # many repos never formally link PRs to issues
        for query in build_queries(owner, repo, keywords, labels, linked):
            if len(found) >= CANDIDATES * 2:
                break
            queries.append(query)
            for item in _search(query):
                if item["number"] != number and "pull_request" not in item:
                    found.setdefault(item["number"], item)
        if len(found) >= CANDIDATES:
            break
    scored = sorted(((similarity(keywords, labels, i), i) for i in found.values()),
                    key=lambda si: -si[0])
    candidates = [i for score, i in scored if score > 0][:CANDIDATES]

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(
            lambda found: _example(owner, repo, found, keywords, labels), candidates))
    examples = [r for r in results if r][:limit]
    files = history_files(examples, paths)
    for ex in examples:  # changed_files keeps the full count
        ex["pr"]["files"] = ex["pr"]["files"][:MAX_FILES_SHOWN]
    return {"repo": f"{owner}/{repo}", "queries": queries, "examples": examples,
            "history_files": files}


def issue_keywords(title: str, body: str, weights_of) -> list[str]:
    """Best search words: title words first (by weight), then strong identifiers from the body.

    `weights_of` is plan_contribution.extract_keywords (passed in to avoid a circular import).
    """
    title_kw = sorted(weights_of(title).items(), key=lambda kv: -kv[1])
    body_kw = [kv for kv in sorted(weights_of(body).items(), key=lambda kv: -kv[1])
               if kv[1] >= 3]
    out: list[str] = []
    for word, _ in title_kw + body_kw:
        if word not in out and not URLISH.search(word):
            out.append(word)
    return out[:MAX_KEYWORDS]


def main() -> int:
    """CLI entry point."""
    from plan_contribution import extract_keywords

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("issue_url", help="https://github.com/OWNER/REPO/issues/N")
    args = ap.parse_args()
    owner, repo, number = parse_issue_url(args.issue_url)
    issue = api_get(f"/repos/{owner}/{repo}/issues/{number}",
                    not_found=f"Issue #{number} not found in {owner}/{repo}.")
    keywords = issue_keywords(issue.get("title", ""), issue.get("body") or "", extract_keywords)
    labels = [lbl["name"] for lbl in issue.get("labels", [])]
    emit_json(find_similar_prs(owner, repo, number, keywords, labels))
    return 0


if __name__ == "__main__":
    run_cli(main)
