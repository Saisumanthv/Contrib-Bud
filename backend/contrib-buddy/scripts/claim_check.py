#!/usr/bin/env python3
"""Check that nobody is already working on an issue before you claim it.

Usage:
    python scripts/claim_check.py https://github.com/OWNER/REPO/issues/123

Looks for overlapping work even without an explicit link: open PRs that reference
the issue, open PRs whose titles share its keywords (and, when files are given,
that change the same files), assignees, and "I'll take this" comments of any age.
It also notes whether a maintainer has replied and whether the issue is too vague
to start on, and writes questions to ask the maintainer first.
Prints JSON: {"verdict", "label", "advice", "findings": [...], "overlapping_prs": [...],
"questions": [...], "vague"}.
Uses about 6 API calls (2 of them searches).
"""

from __future__ import annotations

import argparse
import math
import re
from datetime import UTC, datetime
from typing import Any

from _github import GitHubError, api_get, emit_json, parse_issue_url, run_cli
from find_issues import CLAIM_RE, _parse_time
from rank_issues import ACCEPTANCE_RE, FILE_HINT_RE, REPRO_RE, UNCLEAR_LABELS, score_issue
from similar_prs import is_bot, similarity

MAINTAINER_ROLES = {"OWNER", "MEMBER", "COLLABORATOR"}
RECENT_CLAIM_DAYS = 14   # a claim newer than this means someone is on it
INACTIVE_PR_DAYS = 90    # an open PR untouched this long is probably abandoned
OLD_ISSUE_DAYS = 365
VAGUE_CLARITY = 45       # rank_issues clarity below this: ask before starting
PR_SEARCH_PAGE = 10
FILE_CHECKS = 3          # keyword-matched PRs whose changed files are compared
MAX_QUESTIONS = 4
BUG_RE = re.compile(r"\bbug\b|crash|error|exception|regression|broken|fails?\b|wrong", re.I)
FEATURE_RE = re.compile(r"feature|enhancement|\badd\b|support for|proposal|request", re.I)
PR_LINK_RE = re.compile(r"/pull/(\d+)|\bPR\s*#(\d+)", re.I)
LABELS = {"closed": "Already closed", "taken": "Someone may be on it", "ask_first": "Ask first",
          "clear": "Looks free"}
ADVICE = {
    "closed": "Pick an open issue instead, unless a maintainer says work is left here.",
    "taken": "Don't start yet. Ask whether the existing work is still active, and offer to help.",
    "ask_first": "Post your questions before writing code, and wait for a maintainer to reply.",
    "clear": "No overlapping work found. Claim it politely if the project expects that.",
}


# --- pure helpers -------------------------------------------------------------------

def _days_since(when: str | None, now: datetime) -> int | None:
    t = _parse_time(when)
    return None if t is None else max(0, (now - t).days)


def claims_in(comments: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """Every "I'll take this"-style comment (newest first), with its age in days and the
    line that says so. Quoted lines ("> I'd like to work on this") are someone else's words."""
    out = []
    for c in comments:
        own = [line for line in (c.get("body") or "").splitlines()
               if not line.lstrip().startswith(">")]
        said = next((line.strip() for line in own if CLAIM_RE.search(line)), None)
        if is_bot(c.get("user")) or said is None:
            continue
        out.append({"by": (c.get("user") or {}).get("login"), "at": c.get("created_at"),
                    "days_ago": _days_since(c.get("created_at"), now), "text": said[:160]})
    return sorted(out, key=lambda c: c["at"] or "", reverse=True)


def referencing_prs(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Open PRs that mention the issue (in any repo, e.g. a fork's PR)."""
    seen, out = set(), []
    for event in timeline:
        if event.get("event") != "cross-referenced":
            continue
        source = (event.get("source") or {}).get("issue") or {}
        if "pull_request" not in source or source.get("state") != "open":
            continue
        if source.get("number") in seen:
            continue
        seen.add(source.get("number"))
        out.append(source)
    return out


def common_words(keywords: list[str], prs: list[dict[str, Any]]) -> set[str]:
    """Keywords in most of the found PRs' titles ("diagram" in a diagramming tool):
    sharing them says nothing about overlapping work."""
    if len(prs) < 4:
        return set()
    titles = [(p.get("title") or "").lower() for p in prs]
    return {k for k in keywords if sum(k.lower() in t for t in titles) >= len(titles) / 2}


def pr_summary(pr: dict[str, Any], now: datetime, why: str, strength: str,
               files: list[str] | None = None) -> dict[str, Any]:
    return {"number": pr["number"], "title": pr.get("title"), "url": pr.get("html_url"),
            "author": (pr.get("user") or {}).get("login"), "draft": bool(pr.get("draft")),
            "updated_days_ago": _days_since(pr.get("updated_at"), now),
            "why": why, "strength": strength, "shared_files": files or []}


def gaps(issue: dict[str, Any], comments: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """What the issue is missing before someone can start on it."""
    body = issue.get("body") or ""
    labels = [lbl["name"] if isinstance(lbl, dict) else lbl for lbl in issue.get("labels", [])]
    text = f"{issue.get('title', '')} | {' | '.join(labels)}"
    age = _days_since(issue.get("created_at"), now) or 0
    clarity = score_issue({"body": body, "labels": labels, "comments": len(comments),
                           "age_days": age})["clarity"]
    maintainer_replied = any(c.get("author_association") in MAINTAINER_ROLES
                             and not is_bot(c.get("user")) for c in comments)
    author_is_maintainer = issue.get("author_association") in MAINTAINER_ROLES
    unclear = [lbl for lbl in labels if UNCLEAR_LABELS.search(lbl)]
    return {
        "clarity": clarity, "age_days": age, "unclear_labels": unclear,
        "is_bug": bool(BUG_RE.search(text)), "is_feature": bool(FEATURE_RE.search(text)),
        "has_repro": bool(REPRO_RE.search(body)),
        "has_expected": bool(ACCEPTANCE_RE.search(body) or re.search(r"expected", body, re.I)),
        "points_to_code": bool(FILE_HINT_RE.search(body)),
        "maintainer_engaged": maintainer_replied or author_is_maintainer,
        "vague": clarity < VAGUE_CLARITY or bool(unclear),
    }


def questions_for(g: dict[str, Any], claims: list[dict[str, Any]],
                  overlaps: list[dict[str, Any]], likely_files: list[str]) -> list[str]:
    """Polite questions for the maintainer, most important first."""
    qs = []
    for pr in overlaps[:1]:
        qs.append(f"I noticed #{pr['number']} ({pr['title']}) looks related. Is it still in "
                  "progress, or would a separate PR be welcome?")
    old = [c for c in claims if (c["days_ago"] or 0) > RECENT_CLAIM_DAYS]
    if old and not overlaps:
        c = old[0]
        qs.append(f"@{c['by']} offered to work on this {c['days_ago']} days ago. Are they still "
                  "on it, or is it open for someone else?")
    if g["unclear_labels"]:
        qs.append(f"It's labelled {', '.join(g['unclear_labels'])}: is it ready for a PR, or is "
                  "the approach still being decided?")
    elif not g["maintainer_engaged"]:
        qs.append("Would you accept a PR for this, and is there anything I should know first?")
    if g["vague"] or len(qs) < 2:
        if g["is_bug"] and not g["has_repro"]:
            qs.append("Could you share the steps to reproduce it and the version you saw it on?")
        if not g["has_expected"]:
            qs.append("What should happen instead? Knowing the expected behaviour would help me "
                      "write a test for it." if g["is_bug"] else
                      "What would a finished change look like to you? That would help me keep "
                      "the PR the right size.")
        if g["is_feature"] and not g["points_to_code"]:
            if len(likely_files) >= 2:
                qs.append(f"Should the change go in `{likely_files[0]}` or `{likely_files[1]}`, "
                          "or somewhere else?")
            else:
                qs.append("Do you have a preferred approach or API in mind?")
        if g["age_days"] > OLD_ISSUE_DAYS:
            qs.append(f"This was opened {g['age_days'] // 30} months ago. Does it still "
                      "happen on the latest version?" if g["is_bug"] else
                      f"This was opened {g['age_days'] // 30} months ago. Is it still wanted?")
    return list(dict.fromkeys(qs))[:MAX_QUESTIONS]


def decide(issue: dict[str, Any], claims: list[dict[str, Any]],
           overlaps: list[dict[str, Any]], g: dict[str, Any]) -> tuple[str, list[str]]:
    """(verdict, findings in plain words)."""
    if issue.get("state") == "closed":
        return "closed", ["The issue is already closed."]
    findings, taken, ask = [], False, False
    for a in issue.get("assignees") or []:
        findings.append(f"Assigned to @{a['login']}.")
        taken = True
    for pr in overlaps:
        state = "draft PR" if pr["draft"] else "open PR"
        updated = (f", updated {pr['updated_days_ago']} days ago"
                   if pr["updated_days_ago"] is not None else "")
        findings.append(f"{state} #{pr['number']} by @{pr['author']} {pr['why']}{updated}.")
        if pr["strength"] == "high":
            taken = True
        else:
            ask = True
    for c in claims[:2]:
        when = "today" if c["days_ago"] == 0 else f"{c['days_ago']} days ago"
        findings.append(f"@{c['by']} offered to work on it {when}: \"{c['text']}\"")
        if (c["days_ago"] or 0) <= RECENT_CLAIM_DAYS:
            taken = True
        else:
            ask = True
    if not g["maintainer_engaged"]:
        findings.append("No reply from a maintainer found yet (GitHub can hide private org "
                        "members, so skim the thread).")
    if g["vague"]:
        why = (f"labelled {', '.join(g['unclear_labels'])}" if g["unclear_labels"]
               else f"clarity score {g['clarity']}/100")
        findings.append(f"The issue looks under-specified ({why}).")
        ask = True
    if not findings:
        findings.append("No assignee, related open PR or claim comment found.")
    return ("taken" if taken else "ask_first" if ask else "clear"), findings


# --- GitHub fetching ------------------------------------------------------------------

def _comments(owner: str, repo: str, number: int, count: int) -> list[dict[str, Any]]:
    """The most recent (up to 100) comments: the last page when there are more."""
    page = max(1, math.ceil(count / 100))
    return api_get(f"/repos/{owner}/{repo}/issues/{number}/comments",
                   {"per_page": 100, "page": page})


def _search_prs(query: str) -> list[dict[str, Any]]:
    try:
        return api_get("/search/issues", {"q": query, "per_page": PR_SEARCH_PAGE}).get("items", [])
    except GitHubError as exc:
        if "422" in str(exc):
            return []
        raise


def claim_check(
    owner: str, repo: str, issue: dict[str, Any], keywords: list[str],
    likely_files: list[str] | None = None, now: datetime | None = None,
) -> dict[str, Any]:
    """Look for overlapping work and gaps before claiming `issue` (the GitHub issue object).

    `keywords` are the issue's most specific words, best first (see similar_prs.py);
    `likely_files` are paths a fix probably touches, compared with candidate PRs' files.
    """
    now = now or datetime.now(UTC)
    number = issue["number"]
    likely_files = likely_files or []
    labels = [lbl["name"] for lbl in issue.get("labels", [])]
    comments = _comments(owner, repo, number, issue.get("comments", 0)) if issue.get(
        "comments") else []
    timeline = api_get(f"/repos/{owner}/{repo}/issues/{number}/timeline", {"per_page": 100})

    overlaps = [pr_summary(pr, now, "references this issue", "high")
                for pr in referencing_prs(timeline)]
    seen = {p["number"] for p in overlaps}

    # PRs that never mention the issue but look like the same work.
    base = f"repo:{owner}/{repo} is:pr is:open"
    queries = []
    if len(keywords) >= 2:
        queries.append(f"{base} {keywords[0]} {keywords[1]}")
    queries += [f"{base} {k} in:title" for k in keywords[:1]]
    found: dict[int, dict[str, Any]] = {}
    for query in queries:
        for item in _search_prs(query):
            if item["number"] not in seen and not is_bot(item.get("user")):
                found.setdefault(item["number"], item)
    scored = sorted(((similarity(keywords, labels, pr), pr) for pr in found.values()),
                    key=lambda sp: -sp[0])
    wanted = set(likely_files)
    common = common_words(keywords, list(found.values()))
    for _, pr in [sp for sp in scored if sp[0] > 0][:FILE_CHECKS]:
        files = {f["filename"] for f in api_get(
            f"/repos/{owner}/{repo}/pulls/{pr['number']}/files", {"per_page": 100})}
        shared = sorted(files & wanted)
        title = (pr.get("title") or "").lower()
        title_hits = [k for k in keywords if k.lower() in title and k not in common]
        if shared:
            names = ", ".join(f"`{f}`" for f in shared[:2])
            why = f"changes {names}, which this fix likely touches"
            overlaps.append(pr_summary(pr, now, why, "high" if title_hits else "medium", shared))
        elif len(title_hits) >= 2 and (_days_since(pr.get("updated_at"), now) or 0) \
                <= INACTIVE_PR_DAYS:  # an old PR that only sounds similar is noise
            why = f"has a similar title (shares {', '.join(title_hits[:3])})"
            overlaps.append(pr_summary(pr, now, why, "medium"))

    for pr in overlaps:
        if pr["strength"] == "high" and (pr["updated_days_ago"] or 0) > INACTIVE_PR_DAYS:
            pr["strength"], pr["why"] = "medium", pr["why"] + ", but looks inactive"

    claims = claims_in(comments, now)
    g = gaps(issue, comments, now)
    verdict, findings = decide(issue, claims, overlaps, g)
    return {"verdict": verdict, "label": LABELS[verdict], "advice": ADVICE[verdict],
            "findings": findings, "overlapping_prs": overlaps, "claims": claims[:3],
            "questions": [] if verdict == "closed"
            else questions_for(g, claims, overlaps, likely_files),
            "vague": g["vague"], "maintainer_engaged": g["maintainer_engaged"],
            "clarity": g["clarity"], "queries": queries}


def main() -> int:
    """CLI entry point."""
    from plan_contribution import extract_keywords
    from similar_prs import issue_keywords

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("issue_url", help="https://github.com/OWNER/REPO/issues/N")
    args = ap.parse_args()
    owner, repo, number = parse_issue_url(args.issue_url)
    issue = api_get(f"/repos/{owner}/{repo}/issues/{number}",
                    not_found=f"Issue #{number} not found in {owner}/{repo}.")
    keywords = issue_keywords(issue.get("title", ""), issue.get("body") or "", extract_keywords)
    emit_json(claim_check(owner, repo, issue, keywords))
    return 0


if __name__ == "__main__":
    run_cli(main)
