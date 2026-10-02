"""Difficulty and clarity heuristics."""

from rank_issues import rank_issues, score_issue

CLEAR = {
    "number": 1, "title": "Crash on empty config", "labels": ["good first issue", "bug"],
    "comments": 2, "age_days": 10,
    "body": ("Steps to reproduce:\n1. create an empty `config.toml`\n2. run the CLI\n\n"
             "Expected behavior: a friendly error. The bug is in src/app/config.py where "
             "the parser assumes at least one key.\n```\nTraceback ...\n```\n" + "detail " * 60),
}
VAGUE = {
    "number": 2, "title": "Improve performance", "labels": ["help wanted", "needs-design"],
    "comments": 25, "age_days": 1200, "body": "It is slow.",
}


def test_clear_issue_scores_high_and_easy():
    h = score_issue(CLEAR)
    assert h["clarity"] >= 80
    assert h["difficulty"] == "easy"
    assert any("reproduction" in r for r in h["reasons"])
    assert any("file" in r for r in h["reasons"])


def test_vague_issue_scores_low_and_hard():
    h = score_issue(VAGUE)
    assert h["clarity"] <= 20
    assert h["difficulty"] == "hard"
    assert any("discussion/design" in r for r in h["reasons"])


def test_clarity_is_clamped():
    h = score_issue({"title": "x", "body": "", "labels": ["needs discussion"],
                     "comments": 50, "age_days": 5000})
    assert 0 <= h["clarity"] <= 100


def test_rank_orders_best_first_and_keeps_fields():
    ranked = rank_issues([VAGUE, CLEAR])
    assert [i["number"] for i in ranked] == [1, 2]
    assert ranked[0]["title"] == CLEAR["title"]
    assert ranked[0]["heuristic"]["rank_score"] > ranked[1]["heuristic"]["rank_score"]
