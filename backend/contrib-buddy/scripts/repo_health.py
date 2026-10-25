#!/usr/bin/env python3
"""Judge whether a repo is worth a newcomer's time: Welcoming, Slow or Inactive
(or "unknown" when there are too few outside PRs to judge).

Usage:
    python scripts/repo_health.py OWNER/REPO

Looks at the last commit on the default branch, how quickly someone other than
the author first responded to recent pull requests from outside contributors
(anyone who is not an owner, member or collaborator), how many recently closed
outside PRs were merged, the open PR backlog, and whether a stale bot is configured.
Prints JSON: {"verdict", "label", "summary", "signals": [...], "stale_bot", ...}.
Uses about 10 GitHub API calls (fewer when results are cached).

"Outside contributors" stands in for "first-time contributors": GitHub relabels
a first-timer as CONTRIBUTOR once their PR is merged, so the first-timer label
alone would make every merged first PR invisible.
"""

from __future__ import annotations

import argparse
import re
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

from _github import GitHubError, api_get, emit_json, parse_repo, raw_get, run_cli

MAINTAINER_ROLES = {"OWNER", "MEMBER", "COLLABORATOR"}
RESPONSE_EVENTS = {"commented", "reviewed", "line-commented", "merged", "closed"}
PR_SAMPLE = 100         # recent PRs listed (one API call per list)
TIMELINE_SAMPLE = 6     # outside PRs whose timelines are read for response time
MIN_SAMPLES = 3         # fewer data points than this and a signal is "unknown"
UNANSWERED_DAYS = 14    # an open PR this old with no reply counts as unanswered
STALE_PR_DAYS = 30      # an open PR not updated for this long is part of the backlog
STALE_ACTION_RE = re.compile(r"uses:\s*['\"]?actions/stale@")

LABELS = {"welcoming": "Welcoming", "slow": "Slow", "inactive": "Inactive",
          "unknown": "Not enough data"}


# --- pure helpers -------------------------------------------------------------------

def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _days(delta: timedelta) -> float:
    return round(delta.total_seconds() / 86400, 1)


def is_bot(user: dict[str, Any] | None) -> bool:
    user = user or {}
    return user.get("type") == "Bot" or str(user.get("login", "")).endswith("[bot]")


def outside_prs(pulls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """PRs opened by people who are not maintainers and not bots."""
    return [p for p in pulls
            if not is_bot(p.get("user")) and p.get("author_association") not in MAINTAINER_ROLES]


def merge_rate(pulls: list[dict[str, Any]]) -> tuple[float | None, int]:
    """(share of closed PRs that were merged, number of closed PRs)."""
    closed = [p for p in pulls if p.get("state") == "closed"]
    if not closed:
        return None, 0
    return sum(1 for p in closed if p.get("merged_at")) / len(closed), len(closed)


def first_response(pr: dict[str, Any], timeline: list[dict[str, Any]],
                   now: datetime) -> float | None:
    """Days until someone other than the author (and not a bot) responded to a PR.

    An open PR with no reply after UNANSWERED_DAYS counts as its age (a lower bound);
    a younger unanswered PR, or one the author closed alone, is too early to judge (None).
    """
    created = _parse_time(pr.get("created_at"))
    if not created:
        return None
    author = (pr.get("user") or {}).get("login")
    times = []
    for event in timeline:
        if event.get("event") not in RESPONSE_EVENTS:
            continue
        if event.get("event") == "line-commented":
            event = (event.get("comments") or [{}])[0]
        who = event.get("user") or event.get("actor")
        when = _parse_time(event.get("submitted_at") or event.get("created_at"))
        if who and when and who.get("login") != author and not is_bot(who):
            times.append(when)
    if times:
        return max(0.0, _days(min(times) - created))
    age = _days(now - created)
    if pr.get("state") == "open" and age >= UNANSWERED_DAYS:
        return age
    return None


def stale_workflow_candidates(paths: set[str], workflows: dict[str, str]) -> list[str]:
    """Workflows not yet read whose file name mentions "stale" (worth reading to confirm)."""
    return sorted(p for p in paths
                  if re.fullmatch(r"\.github/workflows/[^/]*stale[^/]*\.ya?ml", p, re.I)
                  and p not in workflows)


def find_stale_bot(paths: set[str], workflows: dict[str, str]) -> dict[str, Any]:
    """Look for probot/stale config or a workflow that uses actions/stale."""
    for path in (".github/stale.yml", ".github/stale.yaml"):
        if path in paths:
            return {"found": True, "source": path}
    for path, text in sorted(workflows.items()):
        if STALE_ACTION_RE.search(text):
            return {"found": True, "source": path}
    return {"found": False, "source": None}


def _signal(key: str, label: str, value: Any, status: str, detail: str) -> dict[str, Any]:
    return {"key": key, "label": label, "value": value, "status": status, "detail": detail}


def _grade(value: float | None, good: float, ok: float, higher_is_better: bool = False) -> str:
    if value is None:
        return "unknown"
    if higher_is_better:
        return "good" if value >= good else "ok" if value >= ok else "bad"
    return "good" if value <= good else "ok" if value <= ok else "bad"


def score(
    *, archived: bool, last_commit_days: float | None, response_days: list[float],
    merged: float | None, closed_count: int, open_prs: int | None, stale_prs: int | None,
    stale_bot: dict[str, Any],
) -> dict[str, Any]:
    """Turn raw numbers into graded signals and a Welcoming / Slow / Inactive verdict."""
    signals = []
    signals.append(_signal(
        "last_commit", "Last commit", last_commit_days, _grade(last_commit_days, 30, 180),
        "unknown" if last_commit_days is None else f"{last_commit_days:g} days ago",
    ))

    median = (round(statistics.median(response_days), 1)
              if len(response_days) >= MIN_SAMPLES else None)
    signals.append(_signal(
        "first_response", "Median first response on PRs", median, _grade(median, 3, 14),
        f"not enough recent outside PRs to tell ({len(response_days)} sampled)" if median is None
        else f"{median:g} days across {len(response_days)} recent outside PRs",
    ))

    rate = merged if closed_count >= MIN_SAMPLES else None
    signals.append(_signal(
        "outside_merge_rate", "Outside contributors' PRs merged", rate,
        _grade(rate, 0.5, 0.25, higher_is_better=True),
        f"not enough closed outside PRs to tell ({closed_count})" if rate is None
        else f"{round(rate * 100)}% of {closed_count} recently closed",
    ))

    backlog = None if not open_prs or stale_prs is None else stale_prs / open_prs
    backlog_status = "good" if open_prs == 0 else _grade(backlog, 0.3, 0.6)
    signals.append(_signal(
        "pr_backlog", "Open PR backlog", {"open": open_prs, "stale": stale_prs}, backlog_status,
        "unknown" if open_prs is None
        else "no open PRs" if open_prs == 0
        else f"{open_prs} open, {stale_prs} untouched for {STALE_PR_DAYS}+ days",
    ))

    signals.append(_signal(
        "stale_bot", "Stale bot", stale_bot["found"], "info",
        f"configured in {stale_bot['source']}: inactive PRs get closed automatically, "
        "so reply to review comments quickly" if stale_bot["found"] else "none found",
    ))

    graded = {s["key"]: s["status"] for s in signals}
    bad = [s for s in signals if s["status"] == "bad"]
    known = sum(1 for k in ("first_response", "outside_merge_rate") if graded[k] != "unknown")
    if archived or graded["last_commit"] == "bad" or (
        graded["first_response"] == "bad" and graded["outside_merge_rate"] == "bad"
    ):
        verdict = "inactive"
    elif not bad and known == 0:
        verdict = "unknown"
    elif not bad and graded["last_commit"] == "good" and "good" in (
        graded["first_response"], graded["outside_merge_rate"]
    ):
        verdict = "welcoming"
    else:
        verdict = "slow"

    if archived:
        summary = "The repository is archived, so it no longer accepts contributions."
    elif verdict == "welcoming":
        summary = "Maintainers are active and respond to outside contributors."
    elif verdict == "unknown":
        summary = ("Too few recent PRs from outside contributors to judge how maintainers "
                   "respond. Ask on an issue before investing a lot of time.")
    elif bad:
        summary = "Watch out: " + "; ".join(
            f"{s['label'][0].lower()}{s['label'][1:]}: {s['detail']}" for s in bad) + "."
    else:
        summary = "The repo is alive, but expect to wait for reviews."
    return {"verdict": verdict, "label": LABELS[verdict], "summary": summary,
            "confidence": ("high", "medium", "low")[2 - known], "signals": signals}


# --- GitHub fetching ------------------------------------------------------------------

def _count(query: str) -> int | None:
    try:
        return api_get("/search/issues", {"q": query, "per_page": 1}).get("total_count")
    except GitHubError:
        return None


def repo_health(
    repo_arg: str, meta: dict[str, Any] | None = None, paths: set[str] | None = None,
    workflows: dict[str, str] | None = None, now: datetime | None = None,
    timeline_sample: int = TIMELINE_SAMPLE,
) -> dict[str, Any]:
    """Assess maintainer responsiveness. `meta`, `paths` and `workflows` can be passed in
    by fetch_repo_docs to avoid fetching them twice; pass `paths=set()` and a smaller
    `timeline_sample` for a quicker check (as repo discovery does). See module docstring."""
    owner, repo = parse_repo(repo_arg)
    now = now or datetime.now(UTC)
    if meta is None:
        meta = api_get(f"/repos/{owner}/{repo}",
                       not_found=f"Repository {owner}/{repo} was not found (or is private).")
        owner, repo = meta["owner"]["login"], meta["name"]
    branch = meta["default_branch"]
    if paths is None:
        tree = api_get(f"/repos/{owner}/{repo}/git/trees/{branch}", {"recursive": "1"})
        paths = {e["path"] for e in tree.get("tree", []) if e.get("type") == "blob"}

    commits = api_get(f"/repos/{owner}/{repo}/commits", {"sha": branch, "per_page": 1})
    last = _parse_time(((commits or [{}])[0].get("commit") or {}).get("committer", {}).get("date"))
    pulls = api_get(f"/repos/{owner}/{repo}/pulls", {
        "state": "all", "sort": "created", "direction": "desc", "per_page": PR_SAMPLE,
    })
    outside = outside_prs(pulls)
    # Recently *closed* PRs, not recently created ones: quick rejections (spam) would
    # otherwise outnumber merges, which take longer.
    closed = api_get(f"/repos/{owner}/{repo}/pulls", {
        "state": "closed", "sort": "updated", "direction": "desc", "per_page": PR_SAMPLE,
    })
    merged, closed_count = merge_rate(outside_prs(closed))

    # PRs old enough to judge: each was either answered or has waited UNANSWERED_DAYS.
    cutoff = now - timedelta(days=UNANSWERED_DAYS)
    old_enough = [p for p in outside if (t := _parse_time(p.get("created_at"))) and t <= cutoff]
    sample = (old_enough or outside)[:timeline_sample]
    with ThreadPoolExecutor(max_workers=max(1, timeline_sample)) as pool:
        timelines = list(pool.map(
            lambda p: api_get(f"/repos/{owner}/{repo}/issues/{p['number']}/timeline",
                              {"per_page": 100}), sample))
    responses = [d for p, t in zip(sample, timelines, strict=True)
                 if (d := first_response(p, t, now)) is not None]

    stale_before = (now - timedelta(days=STALE_PR_DAYS)).strftime("%Y-%m-%d")
    open_prs = _count(f"repo:{owner}/{repo} is:pr is:open")
    stale_prs = _count(f"repo:{owner}/{repo} is:pr is:open updated:<{stale_before}")

    workflows = dict(workflows or {})
    for path in stale_workflow_candidates(paths, workflows)[:3]:
        workflows[path] = raw_get(owner, repo, branch, path) or ""

    result = score(
        archived=bool(meta.get("archived")),
        last_commit_days=_days(now - last) if last else None,
        response_days=responses, merged=merged, closed_count=closed_count,
        open_prs=open_prs, stale_prs=stale_prs,
        stale_bot=find_stale_bot(paths, workflows),
    )
    return {"repo": f"{owner}/{repo}", **result, "sampled_prs": len(pulls),
            "outside_prs": len(outside), "last_commit_at": last.isoformat() if last else None}


def main() -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("repo", help="owner/repo or GitHub URL")
    args = ap.parse_args()
    emit_json(repo_health(args.repo))
    return 0


if __name__ == "__main__":
    run_cli(main)
