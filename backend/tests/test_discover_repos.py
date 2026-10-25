"""Repo discovery: queries, relevance and ranking by health."""

from datetime import UTC, datetime

import discover_repos as dr
import pytest

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def repo(name, language="Python", stars=500, topics=(), description=""):
    return {
        "full_name": f"o/{name}",
        "name": name,
        "html_url": f"https://github.com/o/{name}",
        "language": language,
        "stargazers_count": stars,
        "topics": list(topics),
        "description": description,
        "pushed_at": "2026-09-30T00:00:00Z",
        "default_branch": "main",
        "open_issues_count": 12,
    }


def test_queries_one_per_language_with_interests_ored():
    queries = dr.build_queries(["Python", "C++", "React"], ["data viz", "cli"], NOW)
    assert len(queries) == 2  # React is a word, not a GitHub language
    assert queries[0].startswith('"data viz" OR cli OR react language:python ')
    assert "language:cpp" in queries[1]
    for q in queries:
        assert "archived:false" in q and "good-first-issues:>=2" in q and "pushed:>2026-08-02" in q
    (only_interests,) = dr.build_queries([], ["games"], NOW)
    assert only_interests.startswith("games archived:false")


def test_relevance_prefers_matching_interests_and_languages():
    viz = repo("plotkit", topics=["data-viz", "charts"], description="Data viz for the terminal")
    other = repo("webshop", language="PHP", stars=50000)
    score_viz, langs, interests = dr.relevance(viz, ["Python"], ["data viz"], 0)
    score_other, *_ = dr.relevance(other, ["Python"], ["data viz"], 0)
    assert langs == ["Python"] and interests == ["data viz"] and score_viz > score_other


def test_discover_ranks_by_health_then_fit(monkeypatch):
    results = [
        repo("slowpoke", topics=["cli"]),
        repo("friendly", stars=50),
        repo("ghost", topics=["cli"]),
        repo("newbie"),
    ]
    monkeypatch.setattr(dr, "api_get", lambda path, params=None, **kw: {"items": results})
    verdicts = {
        "o/slowpoke": "slow",
        "o/friendly": "welcoming",
        "o/ghost": "inactive",
        "o/newbie": "unknown",
    }
    seen = {}

    def fake_health(name, meta=None, paths=None, now=None, timeline_sample=6):
        seen[name] = (paths, timeline_sample, meta["default_branch"])
        v = verdicts[name]
        return {
            "verdict": v,
            "label": v.title(),
            "summary": "",
            "confidence": "high",
            "signals": [],
        }

    monkeypatch.setattr(dr, "repo_health", fake_health)
    out = dr.discover_repos("py", "cli", now=NOW)
    assert [r["repo"] for r in out["repos"]] == ["o/friendly", "o/slowpoke", "o/newbie", "o/ghost"]
    assert out["languages"] == ["Python"]
    assert seen["o/friendly"] == (set(), dr.QUICK_TIMELINES, "main")  # the quick check
    assert out["repos"][1]["matched_interests"] == ["cli"]
    assert out["repos"][0]["good_first_issues_url"] == "https://github.com/o/friendly/contribute"


def test_health_failure_keeps_the_repo(monkeypatch):
    monkeypatch.setattr(dr, "api_get", lambda path, params=None, **kw: {"items": [repo("a")]})

    def boom(*a, **kw):
        raise dr.GitHubError("rate limit")

    monkeypatch.setattr(dr, "repo_health", boom)
    (only,) = dr.discover_repos(["python"], [], now=NOW)["repos"]
    assert only["health"]["verdict"] is None and "rate limit" in only["health"]["error"]


def test_needs_something_to_search_for():
    with pytest.raises(dr.GitHubError):
        dr.discover_repos("", " , ")


def test_interests_match_whole_words_only():
    climate = repo("ClimateVision", description="Detect deforestation from imagery")
    _, _, interests = dr.relevance(climate, ["Python"], ["cli"], 0)
    assert interests == []
    tool = repo("doit", topics=["cli", "task-runner"])
    assert dr.relevance(tool, ["Python"], ["cli", "task runner"], 0)[2] == ["cli", "task runner"]
