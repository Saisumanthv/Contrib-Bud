#!/usr/bin/env python3
"""Score issues for clarity and difficulty with transparent heuristics.

Usage:
    python scripts/find_issues.py OWNER/REPO > issues.json
    python scripts/rank_issues.py issues.json        # or: ... | python scripts/rank_issues.py -

Adds a "heuristic" object to each issue: clarity (0-100), difficulty
(easy/medium/hard), rank_score, and the reasons behind each number, then
sorts best-first. No network access. An LLM or agent should refine this
ranking, not replace it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

from _github import GitHubError, emit_json, run_cli

REPRO_RE = re.compile(
    r"steps to reproduce|to reproduce|repro(duction)?\b|expected (behaviou?r|result|output)"
    r"|actual (behaviou?r|result|output)|^\s*1[.)]\s+\S",
    re.IGNORECASE | re.MULTILINE,
)
FILE_HINT_RE = re.compile(
    r"[\w./-]+\.(py|js|jsx|ts|tsx|go|rs|java|kt|rb|php|c|cc|cpp|h|hpp|cs|swift|md|rst|css|scss"
    r"|html|vue|svelte|ya?ml|json|toml)\b"
    r"|https://github\.com/[^\s]+/blob/",
    re.IGNORECASE,
)
ACCEPTANCE_RE = re.compile(
    r"acceptance criteria|- \[ \]|definition of done|should (now )?(be|return|show)",
    re.IGNORECASE,
)

EASY_LABELS = re.compile(r"good[\s_-]*first|first[\s_-]*timer|beginner|\beasy\b|starter|"
                         r"\bdocs?\b|documentation|typo", re.IGNORECASE)
HARD_LABELS = re.compile(r"\bhard\b|complex|difficulty:?\s*(high|hard)|performance|refactor|"
                         r"breaking|security|architecture|needs[\s_-]*design|tracking|epic|\brfc\b",
                         re.IGNORECASE)
UNCLEAR_LABELS = re.compile(r"needs[\s_-]*(discussion|design|reproducer|repro|triage|info|feedback)"
                            r"|feedback wanted|question|discussion|proposal",
                            re.IGNORECASE)
DIFFICULTY_BONUS = {"easy": 10, "medium": 0, "hard": -15}


def score_issue(issue: dict[str, Any]) -> dict[str, Any]:
    """Compute clarity, difficulty and rank_score for one issue (pure function).

    Expects the issue shape produced by find_issues.py (title, body, labels,
    comments, age_days).
    """
    body = issue.get("body") or ""
    labels = " | ".join(issue.get("labels", []))
    comments = int(issue.get("comments", 0))
    age = int(issue.get("age_days", 0))
    clarity, reasons = 30, []

    def adjust(points: int, why: str) -> None:
        nonlocal clarity
        clarity += points
        reasons.append(f"{'+' if points >= 0 else ''}{points} {why}")

    n = len(body.strip())
    if n < 80:
        adjust(-15, "very short description")
    elif n >= 600:
        adjust(15, "detailed description")
    elif n >= 200:
        adjust(10, "reasonable description")
    if REPRO_RE.search(body):
        adjust(15, "has reproduction steps / expected behavior")
    file_hints = len(set(m.group(0) for m in FILE_HINT_RE.finditer(body)))
    if file_hints:
        adjust(15, f"points to {file_hints} file(s)/code location(s)")
    if "```" in body:
        adjust(5, "includes code block")
    if ACCEPTANCE_RE.search(body):
        adjust(10, "states expected outcome / acceptance criteria")
    if EASY_LABELS.search(labels):
        adjust(10, "maintainers labelled it beginner-friendly")
    if UNCLEAR_LABELS.search(labels):
        adjust(-20, "labels say it still needs discussion/design/info")
    if 1 <= comments <= 6:
        adjust(5, "some discussion to learn from")
    elif comments > 15:
        adjust(-10, f"long thread ({comments} comments)")
    if age > 730:
        adjust(-10, f"old issue ({age} days) — check it is still relevant")
    elif age <= 90:
        adjust(5, "recent issue")
    clarity = max(0, min(100, clarity))

    diff_points = 0
    if EASY_LABELS.search(labels):
        diff_points -= 2
    if HARD_LABELS.search(labels):
        diff_points += 2
    if n > 2500:
        diff_points += 1
    if file_hints > 5:
        diff_points += 1
    if comments > 15:
        diff_points += 1
    difficulty = "easy" if diff_points <= -1 else "medium" if diff_points <= 1 else "hard"

    return {
        "clarity": clarity,
        "difficulty": difficulty,
        "rank_score": clarity + DIFFICULTY_BONUS[difficulty],
        "reasons": reasons,
    }


def rank_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return issues with a 'heuristic' field, sorted best-first."""
    scored = [{**i, "heuristic": score_issue(i)} for i in issues]
    return sorted(scored, key=lambda i: (-i["heuristic"]["rank_score"], i.get("age_days", 0)))


def load_issues(source: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load find_issues.py output (object or bare list) from a path or '-' for stdin."""
    try:
        raw = sys.stdin.read() if source == "-" else open(source, encoding="utf-8").read()
        data = json.loads(raw)
    except OSError as exc:
        raise GitHubError(f"Cannot read {source}: {exc.strerror}") from exc
    except ValueError as exc:
        raise GitHubError(f"{source} is not valid JSON (expected find_issues.py output)") from exc
    if isinstance(data, list):
        return {}, data
    if isinstance(data, dict) and isinstance(data.get("issues"), list):
        return data, data["issues"]
    raise GitHubError("Expected find_issues.py output: an object with an 'issues' list.")


def main() -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("source", nargs="?", default="-", help="issues JSON file, or - for stdin")
    args = ap.parse_args()
    meta, issues = load_issues(args.source)
    emit_json({**meta, "issues": rank_issues(issues)})
    return 0


if __name__ == "__main__":
    run_cli(main)
