"""Issue filtering, claim detection and label matching."""

from datetime import UTC, datetime

import find_issues as fi
from conftest import load_fixture

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def _comment(body: str, when: str = "2026-09-28T00:00:00Z", login: str = "dev", kind: str = "User"):
    return {"body": body, "created_at": when, "user": {"login": login, "type": kind}}


def test_filter_skips_assigned_prs_and_linked():
    issues = [
        {"number": 1, "title": "ok", "assignees": []},
        {"number": 2, "title": "assigned", "assignee": {"login": "bob"}, "assignees": [{"login": "bob"}]},
        {"number": 3, "title": "a PR", "pull_request": {}},
        {"number": 4, "title": "linked", "has_linked_pr": True},
    ]
    kept, skipped = fi.filter_issues(issues)
    assert [i["number"] for i in kept] == [1]
    reasons = {s["number"]: s["reason"] for s in skipped}
    assert "@bob" in reasons[2]
    assert "pull request" in reasons[3]
    assert "linked" in reasons[4]


def test_filter_skips_claimed():
    claim = {"by": "newbie", "at": "2026-09-29T10:00:00Z", "text": "can I work on this?"}
    kept, skipped = fi.filter_issues([{"number": 7, "title": "t"}], {7: claim})
    assert kept == []
    assert "@newbie" in skipped[0]["reason"]


def test_find_claim_detects_recent_claims():
    for text in ("I'll take this!", "Can I work on this?", "please assign this to me",
                 "I'm working on it", "/assign", "I would like to work on this issue"):
        assert fi.find_claim([_comment(text)], NOW) is not None, text


def test_find_claim_ignores_old_bots_and_chatter():
    assert fi.find_claim([_comment("I'll take this", when="2026-01-01T00:00:00Z")], NOW) is None
    assert fi.find_claim([_comment("I'll take this", login="bot[bot]", kind="Bot")], NOW) is None
    assert fi.find_claim([_comment("Thanks, this is a great idea")], NOW) is None


def test_match_beginner_labels_orders_and_excludes():
    names = [lbl["name"] for lbl in load_fixture("labels.json")]
    matched = fi.match_beginner_labels(names)
    assert matched[0] == "good first issue"
    assert "help wanted" in matched and "hacktoberfest" in matched
    assert "hacktoberfest-accepted" not in matched
    assert "bug" not in matched


def test_build_search_query_respects_length_limit():
    labels = [f"label number {i}" for i in range(40)]
    q = fi.build_search_query("acme", "widgets", labels, linked_filter=True)
    assert len(q) <= fi.SEARCH_QUERY_MAX
    assert "-linked:pr" in q and "no:assignee" in q


def test_summarize_issue_computes_age_and_truncates():
    issue = load_fixture("search_issues.json")["items"][0]
    out = fi.summarize_issue({**issue, "body": "x" * 5000}, NOW, body_chars=100)
    assert out["age_days"] == 10
    assert out["labels"] == ["good first issue", "documentation"]
    assert out["body"].endswith("[truncated]")


def test_find_issues_end_to_end_with_mocked_api(monkeypatch):
    responses = {
        "/repos/acme/widgets/labels": load_fixture("labels.json"),
        "/search/issues": load_fixture("search_issues.json"),
        "/repos/acme/widgets/issues/101/comments": load_fixture("comments_101.json"),
        "/repos/acme/widgets/issues/102/comments": load_fixture("comments_102.json"),
    }
    monkeypatch.setattr(fi, "api_get", lambda path, params=None, **kw: responses[path])

    result = fi.find_issues("acme/widgets", limit=5, now=NOW)

    numbers = [i["number"] for i in result["issues"]]
    assert numbers == [101, 104]  # 102 claimed by a human, 103 assigned; bot comment ignored
    skipped = {s["number"] for s in result["skipped"]}
    assert skipped == {102, 103}


def test_non_code_labels_are_found_and_get_reserved_slots(monkeypatch):
    names = ["good first issue", "documentation", "i18n", "needs tests", "bug", "docs-accepted"]
    assert fi.match_non_code_labels(names) == ["documentation", "i18n", "needs tests"]

    def issue(number, title):
        return {"number": number, "title": title, "labels": [], "assignees": [], "comments": 0,
                "created_at": "2026-09-20T00:00:00Z", "html_url": f"u/{number}", "body": ""}

    beginner = [issue(n, f"Code task {n}") for n in range(1, 10)]
    non_code = [issue(50, "Translate the help page"), issue(51, "Document the CLI flags")]

    def fake(path, params=None, **kw):
        if path.endswith("/labels"):
            return [{"name": n} for n in names]
        if "documentation" in params["q"]:  # the non-code search
            return {"items": non_code}
        return {"items": beginner}

    monkeypatch.setattr(fi, "api_get", fake)
    result = fi.find_issues("acme/widgets", limit=6, now=NOW)
    numbers = [i["number"] for i in result["issues"]]
    assert numbers == [1, 2, 3, 4, 50, 51]  # 6 // 3 = 2 slots kept for non-code work
    assert result["non_code_labels_used"] == ["documentation", "i18n", "needs tests"]


def test_failed_non_code_search_does_not_break_the_list(monkeypatch):
    def fake(path, params=None, **kw):
        if path.endswith("/labels"):
            return [{"name": "good first issue"}, {"name": "documentation"}]
        if "documentation" in params["q"]:
            raise fi.GitHubError("GitHub API rate limit reached.")
        return {"items": [{"number": 1, "title": "t", "labels": [], "assignees": [],
                           "comments": 0, "created_at": "2026-09-20T00:00:00Z"}]}

    monkeypatch.setattr(fi, "api_get", fake)
    assert [i["number"] for i in fi.find_issues("acme/widgets", now=NOW)["issues"]] == [1]
