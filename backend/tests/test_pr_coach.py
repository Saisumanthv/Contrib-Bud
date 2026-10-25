"""PR coach facts: parsing CI logs and review text, PR feedback and the no-model fixes."""

import pr_coach as co

GHA_PYTEST = """\
2026-10-01T10:00:00.1234567Z ##[group]Run pytest -q
2026-10-01T10:00:01.0000000Z ============================= FAILURES =============================
2026-10-01T10:00:01.0000000Z ___________________________ test_parse_empty ___________________________
2026-10-01T10:00:01.0000000Z     def test_parse_empty():
2026-10-01T10:00:01.0000000Z >       assert parse("") == {}
2026-10-01T10:00:01.0000000Z E       AssertionError: assert None == {}
2026-10-01T10:00:01.0000000Z tests/test_parser.py:12: AssertionError
2026-10-01T10:00:02.0000000Z =========================== short test summary info ===========================
2026-10-01T10:00:02.0000000Z FAILED tests/test_parser.py::test_parse_empty - AssertionError: assert None == {}
2026-10-01T10:00:02.0000000Z ##[error]Process completed with exit code 1.
"""
RUFF_FULL = """\
F401 [*] `os` imported but unused
 --> src/app/util.py:1:8
  |
1 | import os
  |        ^^
E501 Line too long (120 > 100)
 --> src/app/core.py:44:101
Found 2 errors.
"""
ESLINT = """\
/home/runner/work/app/src/components/Button.jsx
  12:5   error  'unused' is assigned a value but never used  no-unused-vars
  30:10  error  Missing semicolon                              semi

✖ 2 problems (2 errors, 0 warnings)
"""


def test_pytest_log_finds_the_failing_test_and_drops_timestamps():
    log = co.parse_log(GHA_PYTEST)
    test = next(e for e in log["errors"] if e["tool"] == "pytest")
    assert test["path"] == "tests/test_parser.py" and test["test"] == "test_parse_empty"
    assert "AssertionError: assert None == {}" in log["excerpt"]
    assert "2026-10-01T" not in log["excerpt"]
    assert log["kinds"][0] == "test"


def test_ruff_full_format_and_eslint_headers_give_file_locations():
    ruff = co.parse_log(RUFF_FULL)["errors"]
    assert [(e["code"], e["path"], e["line"]) for e in ruff] == [
        ("F401", "src/app/util.py", "1"), ("E501", "src/app/core.py", "44")]
    eslint = co.parse_log(ESLINT)["errors"]
    assert {e["path"] for e in eslint} == {"/home/runner/work/app/src/components/Button.jsx"}
    assert [e["code"] for e in eslint] == ["no-unused-vars", "semi"]


def test_other_tools_and_bots():
    cases = {
        "src/a.ts(12,5): error TS2322: Type 'string' is not assignable to type 'number'.": "type",
        "src/b.ts:3:1 - error TS2304: Cannot find name 'foo'.": "type",
        "pkg/x.py:7: error: Incompatible return value type  [return-value]": "type",
        "would reformat src/app.py": "format",
        "[warn] src/index.js": "format",
        "--- FAIL: TestParse (0.00s)": "test",
        "error[E0425]: cannot find value `x` in this scope": "build",
        "ModuleNotFoundError: No module named 'yaml'": "build",
        "npm ERR! code ERESOLVE": "build",
        "DCO check failed: commit abc123 is missing a Signed-off-by line": "dco",
        "CLA not signed: please sign the Contributor License Agreement": "cla",
        "No changeset found. Run `changeset add` to add one": "changelog",
        "✖   subject may not be empty [subject-empty]": "commit_message",
    }
    for line, kind in cases.items():
        assert co.parse_log(line)["kinds"] == [kind], line


def test_innocent_lines_are_not_errors():
    for line in ("Signed-off-by: Jane Dev <jane@example.com>", "✔ CLA signed by all authors",
                 "All checks passed!", "Collected 12 items"):
        assert co.parse_log(line)["errors"] == [], line


def test_rust_location_attaches_to_its_error():
    log = co.parse_log("error[E0425]: cannot find value `x`\n  --> src/main.rs:3:5\n")
    assert log["errors"][0]["path"] == "src/main.rs" and log["errors"][0]["line"] == "3"


def test_long_logs_keep_the_errors_and_drop_the_noise():
    noise = "\n".join(f"downloading package {i}" for i in range(5000))
    log = co.parse_log(noise + "\nFAILED tests/t.py::test_x - KeyError\n" + noise)
    assert "FAILED tests/t.py::test_x" in log["excerpt"]
    assert len(log["excerpt"]) < 1000
    assert co.parse_log(noise)["excerpt"].endswith("downloading package 4999")  # no hits: tail


def test_log_or_review_detection_and_review_parsing():
    assert co.looks_like_log(GHA_PYTEST) and co.looks_like_log(RUFF_FULL + "\nerror: x\nerror: y")
    review = ("Thanks! A few things:\n\nnit: rename `cfg` in src/config.py:14 to `config`.\n\n"
              "```suggestion\nconfig = load()\n```\nAlso see e.g. https://example.com/x.html")
    assert not co.looks_like_log(review)
    parsed = co.parse_review_text(review)
    assert parsed["suggestions"] == ["config = load()"]
    assert parsed["file_refs"] == [{"path": "src/config.py", "line": 14}]
    assert parsed["nits"] == 1


def comment(cid, login, body, reply_to=None, line=10, at="2026-10-01T00:00:00Z"):
    return {"id": cid, "in_reply_to_id": reply_to, "user": {"login": login}, "body": body,
            "path": "src/a.py", "line": line, "original_line": 10, "diff_hunk": "@@\n+x = 1",
            "created_at": at, "html_url": f"https://x/{cid}"}


def test_review_threads_group_replies_and_know_who_is_waiting():
    comments = [comment(1, "maint", "Please add a test", at="2026-10-01T01:00:00Z"),
                comment(2, "me", "Done!", reply_to=1, at="2026-10-01T02:00:00Z"),
                comment(3, "maint", "Rename this", at="2026-10-01T03:00:00Z"),
                comment(4, "maint", "Old note", line=None, at="2026-10-01T04:00:00Z")]
    threads = co.review_threads(comments, "me")
    assert [len(t["comments"]) for t in threads] == [1, 1, 2]  # waiting first, outdated next
    assert threads[0]["comments"][0]["body"] == "Rename this" and threads[0]["waiting_on_you"]
    assert threads[1]["outdated"] and not threads[2]["waiting_on_you"]


def test_latest_review_per_reviewer():
    reviews = [{"user": {"login": "a"}, "state": "CHANGES_REQUESTED", "body": "Needs tests",
                "submitted_at": "2026-10-01T01:00:00Z", "author_association": "MEMBER"},
               {"user": {"login": "a"}, "state": "COMMENTED", "body": "",
                "submitted_at": "2026-10-01T02:00:00Z"},
               {"user": {"login": "b"}, "state": "APPROVED", "body": "LGTM",
                "submitted_at": "2026-10-01T03:00:00Z"},
               {"user": {"login": "me"}, "state": "COMMENTED", "body": "thanks"}]
    latest = co.latest_reviews(reviews, "me")
    assert [(r["reviewer"], r["state"]) for r in latest] == [("a", "CHANGES_REQUESTED"),
                                                             ("b", "APPROVED")]
    assert latest[0]["maintainer"]


def test_fetch_pr_feedback(monkeypatch):
    pr = {"user": {"login": "me"}, "state": "open", "title": "Fix parser", "html_url": "u",
          "head": {"sha": "abc", "ref": "fix/1"}, "base": {"ref": "main", "repo": {"full_name": "o/r"}},
          "mergeable_state": "dirty", "commits": 2, "body": "Fixes #1"}
    runs = {"check_runs": [
        {"id": 9, "name": "lint", "conclusion": "failure", "html_url": "c", "app": {"slug": "github-actions"},
         "output": {"summary": "", "annotations_count": 1}},
        {"id": 10, "name": "tests", "conclusion": "success", "app": {"slug": "github-actions"}}]}
    responses = {
        "/repos/o/r/pulls/5": pr,
        "/repos/o/r/pulls/5/comments": [comment(1, "maint", "Use a constant here")],
        "/repos/o/r/pulls/5/reviews": [],
        "/repos/o/r/issues/5/comments": [{"user": {"login": "dco[bot]", "type": "Bot"},
                                          "body": "DCO missing", "created_at": "t"}],
        "/repos/o/r/commits/abc/check-runs": runs,
        "/repos/o/r/check-runs/9/annotations": [{"path": "src/a.py", "start_line": 3,
                                                 "annotation_level": "failure",
                                                 "message": "F401 unused import"}],
        "/repos/o/r/commits/abc/status": {"statuses": [
            {"context": "DCO", "state": "failure", "description": "sign-off missing"}]},
    }
    monkeypatch.setattr(co, "api_get", lambda path, params=None, **kw: responses[path])
    monkeypatch.setattr(co, "fetch_job_log", lambda *a: RUFF_FULL)
    facts = co.fetch_pr_feedback("https://github.com/o/r/pull/5")
    assert facts["repo"] == "o/r" and facts["pr"]["mergeable_state"] == "dirty"
    assert [c["name"] for c in facts["checks"]] == ["lint", "DCO"]
    assert facts["checks"][0]["kinds"] == ["lint"] and facts["checks"][1]["kinds"] == ["dco"]
    assert facts["checks"][0]["annotations"][0]["message"] == "F401 unused import"
    assert facts["conversation"][0]["bot"] and not facts["logs_need_token"]

    kinds = [g["kind"] for g in co.guidance(facts, {"lint_commands": [
        {"command": "ruff check .", "source": "pyproject.toml"}]})]
    assert kinds == ["dco", "conflict", "lint", "review"]
    lint = next(g for g in co.guidance(facts, {"lint_commands": [
        {"command": "ruff check .", "source": "pyproject.toml"}]}) if g["kind"] == "lint")
    assert lint["commands"] == ["ruff check --fix src/app/core.py src/app/util.py", "ruff check ."]
    assert "`src/app/util.py:1`" in lint["where"]


def test_guidance_from_pasted_log_uses_repo_and_tool_commands():
    facts = co.coach_facts(text=GHA_PYTEST)
    assert facts["pasted"]["kind"] == "log"
    (fix,) = co.guidance(facts)  # the generic "exit code 1" error is dropped
    assert fix["kind"] == "test" and fix["commands"] == ["pytest tests/test_parser.py::test_parse_empty -x"]
    dco = co.guidance(co.coach_facts(text="DCO failed\nerror: commit is missing a sign-off"))
    assert dco[0]["commands"] == ["git rebase --signoff main", "git push --force-with-lease"]


def test_coach_needs_something_to_work_on():
    import pytest
    with pytest.raises(co.GitHubError):
        co.coach_facts(text="   ")
