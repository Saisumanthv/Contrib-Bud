#!/usr/bin/env python3
"""Suggest welcoming repos to contribute to, from the languages and interests you give.

Usage:
    python scripts/discover_repos.py --languages "python, typescript" --interests "cli, data viz"

Searches GitHub for active, non-archived repos in your languages that match your
interests and have open "good first issue" issues, keeps the most relevant, runs a
quick health check on each (repo_health.py with a smaller sample) and ranks them:
Welcoming first, then Slow, then too-new-to-tell, with Inactive last.
Prints JSON: {"languages", "interests", "queries", "repos": [...]}.
Uses 1 search per language (at most 3) plus about 8 calls per suggested repo.
"""

from __future__ import annotations

import argparse
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

from _github import GitHubError, api_get, emit_json, run_cli
from match_skills import normalize_skills
from repo_health import repo_health

MAX_LANGUAGES = 3
MAX_INTERESTS = 4
PER_QUERY = 15
SUGGESTIONS = 6
ACTIVE_DAYS = 60  # pushed within this many days
MIN_STARS = 20  # filters out personal experiments
MIN_GOOD_FIRST = 2  # open "good first issue" issues
QUICK_TIMELINES = 3  # PR timelines read per repo for the quick health check
VERDICT_ORDER = {"welcoming": 0, "slow": 1, "unknown": 2, None: 3, "inactive": 4}

# Our technology names -> GitHub's `language:` qualifier. Frameworks aren't languages on
# GitHub, so they become search words instead (React -> "react").
SEARCH_LANGUAGE = {
    "Python": "python",
    "JavaScript": "javascript",
    "TypeScript": "typescript",
    "Go": "go",
    "Rust": "rust",
    "Java": "java",
    "Kotlin": "kotlin",
    "C": "c",
    "C++": "cpp",
    "C#": "csharp",
    "Ruby": "ruby",
    "PHP": "php",
    "Swift": "swift",
    "Dart": "dart",
    "Shell": "shell",
    "HTML": "html",
    "CSS": "css",
    "Vue": "vue",
    "Svelte": "svelte",
}
FRAMEWORK_WORDS = {
    "React": "react",
    "Docker": "docker",
    "SQL": "database",
    "CI/YAML": "github-actions",
    "Markdown/Writing": "documentation",
}


def _term(text: str) -> str:
    text = text.strip().replace('"', "")
    return f'"{text}"' if re.search(r"\s", text) else text


def build_queries(languages: list[str], interests: list[str], now: datetime) -> list[str]:
    """One repository search per language, the interests OR-ed together."""
    words = [_term(i) for i in interests[:MAX_INTERESTS] if i.strip()]
    words += [FRAMEWORK_WORDS[lang] for lang in languages if lang in FRAMEWORK_WORDS]
    pushed = (now - timedelta(days=ACTIVE_DAYS)).strftime("%Y-%m-%d")
    base = (
        f"archived:false is:public fork:false pushed:>{pushed} stars:>={MIN_STARS} "
        f"good-first-issues:>={MIN_GOOD_FIRST}"
    )
    text = " OR ".join(dict.fromkeys(words))
    qualifiers = [
        f"language:{_term(SEARCH_LANGUAGE[lang])}" for lang in languages if lang in SEARCH_LANGUAGE
    ][:MAX_LANGUAGES] or [""]
    return [" ".join(p for p in (text, q, base) if p) for q in qualifiers]


def relevance(
    repo: dict[str, Any], languages: list[str], interests: list[str], position: int
) -> tuple[float, list[str], list[str]]:
    """(score, matched languages, matched interests) for a search result."""
    haystack = (
        " ".join(
            [repo.get("name") or "", repo.get("description") or ""] + (repo.get("topics") or [])
        )
        .lower()
        .replace("-", " ")
    )
    def has_word(word: str) -> bool:  # whole words: "cli" must not match "climate"
        return re.search(rf"\b{re.escape(word)}\b", haystack) is not None

    matched_interests = [
        i
        for i in interests
        if i.strip() and all(has_word(w) for w in i.lower().replace("-", " ").split())
    ]
    repo_lang = (repo.get("language") or "").lower()
    matched_languages = [
        lang
        for lang in languages  # GitHub names match ours ("C++", "Vue")
        if lang.lower() == repo_lang
        or (lang in FRAMEWORK_WORDS and has_word(FRAMEWORK_WORDS[lang].replace("-", " ")))
    ]
    score = (
        3 * len(matched_interests)
        + 2 * len(matched_languages)
        + math.log10(1 + (repo.get("stargazers_count") or 0))
        - 0.3 * position
    )
    return score, matched_languages, matched_interests


def summarize(
    repo: dict[str, Any], now: datetime, matched_languages: list[str], matched_interests: list[str]
) -> dict[str, Any]:
    pushed = repo.get("pushed_at")
    days = None
    if pushed:
        days = (now - datetime.fromisoformat(pushed.replace("Z", "+00:00"))).days
    return {
        "repo": repo["full_name"],
        "url": repo.get("html_url"),
        "description": repo.get("description"),
        "language": repo.get("language"),
        "topics": (repo.get("topics") or [])[:8],
        "stars": repo.get("stargazers_count"),
        "open_issues": repo.get("open_issues_count"),
        "pushed_days_ago": days,
        "license": (repo.get("license") or {}).get("spdx_id"),
        "matched_languages": matched_languages,
        "matched_interests": matched_interests,
        "good_first_issues_url": f"{repo.get('html_url')}/contribute",
    }


def _quick_health(repo: dict[str, Any], now: datetime) -> dict[str, Any]:
    try:
        h = repo_health(
            repo["full_name"], meta=repo, paths=set(), now=now, timeline_sample=QUICK_TIMELINES
        )
    except GitHubError as exc:
        return {"verdict": None, "label": "Unknown", "error": str(exc)}
    return {k: h[k] for k in ("verdict", "label", "summary", "confidence", "signals")}


def discover_repos(
    languages: list[str] | str,
    interests: list[str] | str,
    limit: int = SUGGESTIONS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Welcoming repos for these languages and interests. See module docstring."""
    now = now or datetime.now(UTC)
    langs = normalize_skills(languages)
    wants = [
        i.strip()
        for i in (re.split(r"[,;\n]+", interests) if isinstance(interests, str) else interests)
        if i.strip()
    ]
    if not langs and not wants:
        raise GitHubError("Tell me at least one language or interest, e.g. 'python' or 'cli'.")
    queries = build_queries(langs, wants, now)
    found: dict[str, tuple[float, dict[str, Any], list[str], list[str]]] = {}
    for query in queries:
        items = api_get("/search/repositories", {"q": query, "per_page": PER_QUERY}).get(
            "items", []
        )
        for position, repo in enumerate(items):
            score, ml, mi = relevance(repo, langs, wants, position)
            if repo["full_name"] not in found or found[repo["full_name"]][0] < score:
                found[repo["full_name"]] = (score, repo, ml, mi)
    best = sorted(found.values(), key=lambda f: -f[0])[:limit]

    with ThreadPoolExecutor(max_workers=3) as pool:
        healths = list(pool.map(lambda f: _quick_health(f[1], now), best))
    repos = [
        {**summarize(repo, now, ml, mi), "fit_score": round(score, 2), "health": health}
        for (score, repo, ml, mi), health in zip(best, healths, strict=True)
    ]
    repos.sort(key=lambda r: (VERDICT_ORDER.get(r["health"]["verdict"], 3), -r["fit_score"]))
    return {"languages": langs, "interests": wants, "queries": queries, "repos": repos}


def main() -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--languages", default="", help='e.g. "python, typescript, react"')
    ap.add_argument("--interests", default="", help='e.g. "cli, data visualization, games"')
    ap.add_argument("--limit", type=int, default=SUGGESTIONS, help="repos to suggest (max 10)")
    args = ap.parse_args()
    emit_json(discover_repos(args.languages, args.interests, max(1, min(args.limit, 10))))
    return 0


if __name__ == "__main__":
    run_cli(main)
