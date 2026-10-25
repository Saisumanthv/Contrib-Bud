"""Similar merged PRs: queries, ranking, finding the fixing PR and the file history."""

import plan_contribution as pc
import similar_prs as sp


def found(number, title, body="", labels=()):
    return {"number": number, "title": title, "body": body, "html_url": f"https://x/{number}",
            "labels": [{"name": n} for n in labels]}


def example(number, files, large=False):
    return {"pr": {"number": number, "large": large, "files": [{"path": f} for f in files]}}


def test_queries_go_from_precise_to_broad():
    queries = sp.build_queries("o", "r", ["gantt", "config.ts", "x"], ["good first issue", "area: gantt"])
    assert queries == [
        'repo:o/r is:issue is:closed linked:pr gantt "config.ts"',
        "repo:o/r is:issue is:closed linked:pr gantt in:title",
        'repo:o/r is:issue is:closed linked:pr "config.ts" in:title',
        'repo:o/r is:issue is:closed linked:pr label:"area: gantt"',  # generic label dropped
    ]
    assert "linked:pr" not in sp.build_queries("o", "r", ["gantt"], [], linked=False)[0]


def test_similarity_needs_a_title_or_label_match():
    keywords = ["gantt", "milestone"]
    assert sp.similarity(keywords, [], found(1, "Gantt milestone crash")) == 6
    assert sp.similarity(keywords, [], found(2, "Unrelated", body="gantt milestone")) == 0
    assert sp.similarity(keywords, ["area: gantt"], found(3, "Other", labels=["area: gantt"])) == 3


def test_issue_keywords_prefer_title_and_skip_urls():
    body = "See https://example.org/docs.html and `parseDate` in `src/utils/date.ts`."
    words = sp.issue_keywords("Gantt dateFormat is ignored", body, pc.extract_keywords)
    assert words[0] == "dateformat"
    assert "gantt" in words
    assert not any("example.org" in w for w in words)


def test_merged_pr_from_cross_reference():
    timeline = [
        {"event": "cross-referenced", "source": {"issue": {
            "number": 9, "pull_request": {"merged_at": None}, "repository": {"full_name": "O/R"}}}},
        {"event": "cross-referenced", "source": {"issue": {
            "number": 10, "pull_request": {"merged_at": "2026-01-02T00:00:00Z"},
            "repository": {"full_name": "fork/r"}}}},
        {"event": "cross-referenced", "source": {"issue": {
            "number": 11, "title": "Fix", "pull_request": {"merged_at": "2026-01-03T00:00:00Z"},
            "repository": {"full_name": "O/R"}}}},
    ]
    pr, shas = sp.merged_pr_from_timeline(timeline, "o/r")
    assert pr["number"] == 11 and pr["merged_at"] == "2026-01-03T00:00:00Z" and shas == []


def test_commit_fallback_when_no_cross_reference():
    timeline = [{"event": "referenced", "commit_id": "aaa"}, {"event": "referenced", "commit_id": "bbb"},
                {"event": "closed", "commit_id": "ccc"}]
    pr, shas = sp.merged_pr_from_timeline(timeline, "o/r")
    assert pr is None and shas == ["ccc", "bbb", "aaa"]  # closing commit first, then newest
    pulls = [{"number": 5, "merged_at": None, "base": {"repo": {"full_name": "o/r"}}},
             {"number": 6, "merged_at": "2026-01-01T00:00:00Z", "base": {"repo": {"full_name": "o/r"}}}]
    assert sp.merged_pr_from_commit(pulls, "o/r")["number"] == 6


def test_reviews_ignore_author_and_bots():
    reviews = [{"user": {"login": "me"}, "state": "COMMENTED"},
               {"user": {"login": "ci-bot"}, "state": "APPROVED"},
               {"user": {"login": "maint"}, "state": "CHANGES_REQUESTED"},
               {"user": {"login": "maint"}, "state": "APPROVED"}]
    summary = sp.summarize_reviews(reviews, "me")
    assert summary == {"count": 2, "approvals": 1, "changes_requested": 1, "comments": 0,
                       "reviewers": ["maint"]}
    story = sp.review_story({"days_to_merge": 2.5, "reviews": summary})
    assert story == "merged in 2.5 days after 2 review(s) from @maint including 1 request(s) for changes"


def test_history_files_rank_shared_files_and_skip_noise():
    examples = [example(1, ["src/gantt.ts", "package-lock.json", "gone.ts"]),
                example(2, ["src/gantt.ts", "tests/gantt.spec.ts"]),
                example(3, ["src/everything.ts"], large=True)]
    files = sp.history_files(examples, {"src/gantt.ts", "tests/gantt.spec.ts", "src/everything.ts"})
    assert files == [{"path": "src/gantt.ts", "prs": [1, 2]},
                     {"path": "tests/gantt.spec.ts", "prs": [2]}]


def test_find_similar_prs_end_to_end(monkeypatch):
    issue = found(7, "Gantt milestone breaks", labels=["area: gantt"])

    def fake_get(path, params=None, **kw):
        if path == "/search/issues":
            return {"items": [issue, found(1, "Me again")]}
        if path.endswith("/issues/7/timeline"):
            return [{"event": "closed", "commit_id": "abc"}]
        if path.endswith("/commits/abc/pulls"):
            return [{"number": 40, "title": "fix: gantt", "html_url": "https://x/pr/40",
                     "user": {"login": "dev"}, "author_association": "CONTRIBUTOR",
                     "created_at": "2026-01-01T00:00:00Z", "merged_at": "2026-01-03T00:00:00Z",
                     "base": {"repo": {"full_name": "o/r"}}}]
        if path.endswith("/pulls/40/files"):
            return [{"filename": "src/gantt.ts", "additions": 5, "deletions": 1}]
        if path.endswith("/pulls/40/reviews"):
            return [{"user": {"login": "maint"}, "state": "APPROVED"}]
        raise AssertionError(path)

    monkeypatch.setattr(sp, "api_get", fake_get)
    result = sp.find_similar_prs("o", "r", 1, ["gantt", "milestone"], ["area: gantt"], {"src/gantt.ts"})
    (ex,) = result["examples"]
    assert ex["issue"]["number"] == 7 and ex["pr"]["number"] == 40
    assert ex["pr"]["days_to_merge"] == 2.0 and ex["pr"]["additions"] == 5
    assert "gantt" in ex["why"] and "area: gantt" in ex["why"]
    assert result["history_files"] == [{"path": "src/gantt.ts", "prs": [40]}]


def test_plan_puts_history_files_first():
    history = [{"path": "src/gantt.ts", "prs": [40]}]
    matched = [{"path": "src/gantt.ts", "score": 9, "matched": ["gantt"], "source": "path match"},
               {"path": "src/other.ts", "score": 3, "matched": ["other"], "source": "path match"}]
    merged = pc.merge_likely_files(history, matched)
    assert [(f["path"], f["source"]) for f in merged] == [("src/gantt.ts", "history"),
                                                          ("src/other.ts", "path match")]
    assert "changed by similar merged PR #40" in pc._likely_line(merged[0])
    tie = [{"path": "docs/conf.py", "prs": [1]}, {"path": "src/core.py", "prs": [1]}]
    assert pc.merge_likely_files(tie, [])[0]["path"] == "src/core.py"


def test_format_similar_handles_empty_and_errors():
    assert "No closed issue" in pc.format_similar({"examples": []})
    assert "rate limit" in pc.format_similar({"error": "rate limit"})
