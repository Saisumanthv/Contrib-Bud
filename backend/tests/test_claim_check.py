"""Pre-claim check: overlapping PRs, claims, vague issues and the questions to ask."""

from datetime import UTC, datetime

import claim_check as cc
import plan_contribution as pc

NOW = datetime(2026, 10, 1, tzinfo=UTC)
CLEAR_BODY = ("Steps to reproduce:\n1. Run `click --opt`\n\nExpected behavior: the value from the "
              "callback is used.\n\nThe code is in src/click/core.py. " + "Detail. " * 40)


def issue(body=CLEAR_BODY, labels=("bug",), assignees=(), comments=2, role="NONE",
          created="2026-09-01T00:00:00Z", title="Option callback value is ignored"):
    return {"number": 7, "title": title, "body": body, "labels": [{"name": n} for n in labels],
            "assignees": [{"login": a} for a in assignees], "comments": comments,
            "author_association": role, "created_at": created}


def comment(body, when="2026-09-28T00:00:00Z", login="dev", role="NONE", kind="User"):
    return {"body": body, "created_at": when, "author_association": role,
            "user": {"login": login, "type": kind}}


def pr(number, title, state="open", updated="2026-09-25T00:00:00Z", login="other", draft=False):
    return {"number": number, "title": title, "state": state, "updated_at": updated,
            "html_url": f"https://github.com/o/r/pull/{number}", "draft": draft,
            "pull_request": {}, "user": {"login": login, "type": "User"}, "labels": []}


def fake_github(monkeypatch, comments=(), timeline=(), search=(), files=None):
    files = files or {}

    def get(path, params=None, **kw):
        if path.endswith("/comments"):
            return list(comments)
        if path.endswith("/timeline"):
            return list(timeline)
        if path == "/search/issues":
            return {"items": list(search)}
        if "/pulls/" in path and path.endswith("/files"):
            return [{"filename": f} for f in files.get(int(path.split("/")[-2]), [])]
        raise AssertionError(path)

    monkeypatch.setattr(cc, "api_get", get)


def run(monkeypatch, the_issue=None, likely=("src/click/core.py",), **github):
    fake_github(monkeypatch, **github)
    return cc.claim_check("o", "r", the_issue or issue(), ["callback", "option"], list(likely), NOW)


MAINTAINER_OK = comment("Thanks, a PR is welcome!", login="maint", role="MEMBER")


def test_clear_issue_with_maintainer_reply_looks_free(monkeypatch):
    result = run(monkeypatch, comments=[MAINTAINER_OK])
    assert result["verdict"] == "clear" and result["questions"] == []


def test_pr_referencing_the_issue_means_taken(monkeypatch):
    timeline = [{"event": "cross-referenced", "source": {"issue": pr(45, "Fix callback")}}]
    result = run(monkeypatch, comments=[MAINTAINER_OK], timeline=timeline)
    assert result["verdict"] == "taken"
    assert "#45 by @other references this issue" in " ".join(result["findings"])
    assert "#45" in result["questions"][0]


def test_inactive_referencing_pr_only_asks_first(monkeypatch):
    stale = pr(45, "Fix callback", updated="2026-03-01T00:00:00Z")
    timeline = [{"event": "cross-referenced", "source": {"issue": stale}}]
    result = run(monkeypatch, comments=[MAINTAINER_OK], timeline=timeline)
    assert result["verdict"] == "ask_first"
    assert "looks inactive" in result["overlapping_prs"][0]["why"]


def test_unlinked_pr_touching_the_same_file_is_found(monkeypatch):
    search = [pr(50, "Use option callback result"), pr(51, "Unrelated docs tweak")]
    result = run(monkeypatch, comments=[MAINTAINER_OK], search=search,
                 files={50: ["src/click/core.py"], 51: ["docs/x.md"]})
    (overlap,) = result["overlapping_prs"]
    assert overlap["number"] == 50 and overlap["shared_files"] == ["src/click/core.py"]
    assert overlap["strength"] == "high" and result["verdict"] == "taken"


def test_similar_title_without_shared_files_is_a_maybe(monkeypatch):
    search = [pr(52, "Option callback cleanup")]
    result = run(monkeypatch, comments=[MAINTAINER_OK], search=search, files={52: ["a.py"]})
    assert result["overlapping_prs"][0]["strength"] == "medium"
    assert result["verdict"] == "ask_first"


def test_recent_claim_is_taken_but_an_old_one_is_a_question(monkeypatch):
    recent = run(monkeypatch, comments=[MAINTAINER_OK, comment("Can I work on this?")])
    assert recent["verdict"] == "taken" and recent["claims"][0]["by"] == "dev"
    old = run(monkeypatch, comments=[MAINTAINER_OK,
                                     comment("I'll take this", when="2026-06-01T00:00:00Z")])
    assert old["verdict"] == "ask_first"
    assert any("@dev offered to work on this 122 days ago" in q for q in old["questions"])


def test_bots_and_assignees(monkeypatch):
    bot = comment("I'll take this", login="stale[bot]", kind="Bot")
    assert run(monkeypatch, comments=[MAINTAINER_OK, bot])["claims"] == []
    result = run(monkeypatch, issue(assignees=["bob"]), comments=[MAINTAINER_OK])
    assert result["verdict"] == "taken" and "Assigned to @bob." in result["findings"]


def test_vague_issue_gets_questions(monkeypatch):
    vague = issue(body="It doesn't work.", labels=("bug", "needs triage"))
    result = run(monkeypatch, vague, comments=[])
    assert result["vague"] and result["verdict"] == "ask_first"
    text = " ".join(result["questions"])
    assert "needs triage" in text and "steps to reproduce" in text and "What should happen" in text
    assert len(result["questions"]) <= cc.MAX_QUESTIONS


def test_no_maintainer_reply_asks_if_a_pr_is_welcome(monkeypatch):
    result = run(monkeypatch, comments=[comment("+1, same here")])
    assert any(f.startswith("No reply from a maintainer found yet") for f in result["findings"])
    assert result["questions"][0].startswith("Would you accept a PR")


def test_feature_without_code_pointers_asks_where_it_goes(monkeypatch):
    feature = issue(body="Add support for colors in help output.", labels=("enhancement",),
                    title="Add colored help")
    result = run(monkeypatch, feature, likely=("src/click/formatting.py", "src/click/core.py"),
                 comments=[MAINTAINER_OK])
    assert any("`src/click/formatting.py` or `src/click/core.py`" in q for q in result["questions"])


def test_claim_comment_includes_questions_or_checks_in():
    asking = {"verdict": "ask_first", "questions": ["Is this still wanted?", "Which version?"]}
    text = pc._claim_comment(asking)
    assert "Before I start, could you help with a few questions?" in text
    assert "1. Is this still wanted?\n2. Which version?" in text
    assert pc.PLAN_PLACEHOLDER in text  # the model fills this in later

    taken = {"verdict": "taken", "questions": [],
             "overlapping_prs": [{"number": 45, "author": "other", "strength": "high"}]}
    text = pc._claim_comment(taken)
    assert "I noticed #45 by @other. Is that still in progress?" in text

    assert "Before I start" not in pc._claim_comment({"verdict": "clear", "questions": []})


def test_format_claim_check():
    md = pc.format_claim_check({"verdict": "ask_first", "label": "Ask first", "advice": "Ask.",
                                "findings": ["No maintainer has replied on the issue yet."]})
    assert md.startswith("**✋ Ask first.** Ask.") and "- No maintainer has replied" in md
    assert "Could not check" in pc.format_claim_check({"verdict": None, "error": "rate limit"})


def test_model_questions_replace_the_templates_in_the_comment(monkeypatch):
    from buddy import cli, llm

    assert llm.validate_plan({"approach": "a", "pr_summary": "b", "claim_one_line": "c"}) == []
    assert llm.validate_plan({"approach": "a", "pr_summary": "b", "claim_one_line": "c",
                              "questions": ["", "x"]})
    check = {"verdict": "ask_first", "questions": ["What should happen instead?"]}
    data = {"pr_description": "Fixes #7\n\nbody", "claim_check": check,
            "claim_comment": pc._claim_comment(check)}
    ai = {"pr_summary": "Use the callback value.", "claim_one_line": "use the callback's value.",
          "approach": "1. Do it", "questions": ["Should `--opt` keep the callback's return value?"]}
    monkeypatch.setattr(cli.planner, "render_markdown", lambda d, approach=None: "md")
    cli.apply_refinement(data, ai)
    assert "1. Should `--opt` keep the callback's return value?" in data["claim_comment"]
    assert "My plan is to use the callback's value." in data["claim_comment"]


def test_closed_issue_is_not_claimable(monkeypatch):
    closed = {**issue(), "state": "closed"}
    result = run(monkeypatch, closed, comments=[comment("Can I work on this?")])
    assert result["verdict"] == "closed" and result["questions"] == []
    assert "is closed" in pc._claim_comment(result)


def test_ubiquitous_words_and_stale_lookalikes_are_not_overlaps(monkeypatch):
    # "option" is in every found title, so only "callback" is distinctive: not enough.
    search = [pr(60, "Option callback tweak"), pr(61, "Option docs"), pr(62, "Option typing"),
              pr(63, "Option help text")]
    assert cc.common_words(["callback", "option"], search) == {"option"}
    result = run(monkeypatch, comments=[MAINTAINER_OK], search=search, files={})
    assert result["overlapping_prs"] == []
    stale = [pr(70, "Fix option callback value", updated="2025-01-01T00:00:00Z")]
    assert run(monkeypatch, comments=[MAINTAINER_OK], search=stale, files={})["overlapping_prs"] == []


def test_quoted_claims_are_not_claims(monkeypatch):
    reply = comment("> I'd like to work on this issue\n\nNoted, thank you.", login="fan")
    result = run(monkeypatch, comments=[MAINTAINER_OK, reply])
    assert result["claims"] == [] and result["verdict"] == "clear"
    real = comment("Thanks for the details!\nI'd like to work on this, if that's ok.")
    (claim,) = run(monkeypatch, comments=[MAINTAINER_OK, real])["claims"]
    assert claim["text"] == "I'd like to work on this, if that's ok."  # the matching line


def test_non_bug_questions_fit_the_issue(monkeypatch):
    policy = issue(body="We should write down a policy for AI-assisted PRs.", labels=("discussion",),
                   title="Policy for AI-assisted pull requests", created="2025-01-01T00:00:00Z")
    text = " ".join(run(monkeypatch, policy, comments=[MAINTAINER_OK])["questions"])
    assert "still happen" not in text and "What should happen instead" not in text
    assert "Is it still wanted?" in text and "finished change" in text
