"""Repo health: response times, merge rate, stale bots and the verdict."""

from datetime import UTC, datetime

import repo_health as rh

NOW = datetime(2026, 10, 1, tzinfo=UTC)
NO_BOT = {"found": False, "source": None}


def pr(number, state="closed", merged=True, role="CONTRIBUTOR", login="newbie",
       created="2026-09-20T00:00:00Z", kind="User"):
    return {"number": number, "state": state, "merged_at": "2026-09-22T00:00:00Z" if merged else None,
            "author_association": role, "created_at": created,
            "user": {"login": login, "type": kind}}


def verdict(**overrides):
    args = dict(archived=False, last_commit_days=2, response_days=[1, 2, 1], merged=0.8,
                closed_count=5, open_prs=10, stale_prs=2, stale_bot=NO_BOT)
    return rh.score(**{**args, **overrides})


def test_outside_prs_drop_maintainers_and_bots():
    pulls = [pr(1), pr(2, role="MEMBER"), pr(3, login="dependabot[bot]", kind="Bot"),
             pr(4, role="FIRST_TIME_CONTRIBUTOR")]
    assert [p["number"] for p in rh.outside_prs(pulls)] == [1, 4]


def test_merge_rate_ignores_open_prs():
    rate, closed = rh.merge_rate([pr(1), pr(2, merged=False), pr(3, state="open", merged=False)])
    assert (rate, closed) == (0.5, 2)
    assert rh.merge_rate([pr(1, state="open")]) == (None, 0)


def test_first_response_skips_author_and_bots():
    timeline = [
        {"event": "commented", "user": {"login": "newbie"}, "created_at": "2026-09-20T05:00:00Z"},
        {"event": "commented", "user": {"login": "ci[bot]", "type": "Bot"},
         "created_at": "2026-09-20T06:00:00Z"},
        {"event": "reviewed", "user": {"login": "maint"}, "submitted_at": "2026-09-22T12:00:00Z"},
        {"event": "labeled", "actor": {"login": "maint"}, "created_at": "2026-09-20T01:00:00Z"},
    ]
    assert rh.first_response(pr(1), timeline, NOW) == 2.5


def test_first_response_for_unanswered_prs():
    old = pr(1, state="open", merged=False, created="2026-09-01T00:00:00Z")
    young = pr(2, state="open", merged=False, created="2026-09-28T00:00:00Z")
    assert rh.first_response(old, [], NOW) == 30.0       # counts as at least its age
    assert rh.first_response(young, [], NOW) is None     # too early to tell


def test_find_stale_bot():
    assert rh.find_stale_bot({".github/stale.yml"}, {})["source"] == ".github/stale.yml"
    flow = {".github/workflows/triage.yml": "steps:\n  - uses: actions/stale@v9\n"}
    assert rh.find_stale_bot(set(), flow)["source"] == ".github/workflows/triage.yml"
    assert not rh.find_stale_bot({".github/workflows/close-stale.yml"}, {})["found"]  # unread
    cache = ".github/workflows/cleanup_stale_branch_caches.yml"
    assert rh.stale_workflow_candidates({cache, ".github/workflows/ci.yml"}, {}) == [cache]
    assert rh.find_stale_bot({"src/stale.py"}, {}) == NO_BOT


def test_active_responsive_repo_is_welcoming():
    result = verdict()
    assert result["verdict"] == "welcoming" and result["confidence"] == "high"


def test_slow_reviews_make_a_repo_slow():
    result = verdict(response_days=[20, 30, 25])
    assert result["verdict"] == "slow"
    assert "median first response" in result["summary"]


def test_archived_or_abandoned_repos_are_inactive():
    assert verdict(archived=True)["verdict"] == "inactive"
    assert verdict(last_commit_days=400)["verdict"] == "inactive"
    assert verdict(response_days=[30, 40, 50], merged=0.1)["verdict"] == "inactive"


def test_too_little_data_says_so():
    result = verdict(response_days=[1], closed_count=1)
    assert result["verdict"] == "unknown" and result["confidence"] == "low"


def test_backlog_grading():
    signals = {s["key"]: s for s in verdict(open_prs=10, stale_prs=9)["signals"]}
    assert signals["pr_backlog"]["status"] == "bad"
    signals = {s["key"]: s for s in verdict(open_prs=0, stale_prs=0)["signals"]}
    assert signals["pr_backlog"]["status"] == "good"


def test_repo_health_end_to_end(monkeypatch):
    responses = {
        "/repos/o/r/commits": [{"commit": {"committer": {"date": "2026-09-30T00:00:00Z"}}}],
    }
    old = "2026-09-10T00:00:00Z"
    pulls = {"all": [pr(2, state="open", merged=False), pr(1, created=old), pr(3, role="OWNER"),
                     pr(4, created=old), pr(9, created=old)],
             "closed": [pr(5), pr(6), pr(7, merged=False), pr(8, role="MEMBER", merged=False)]}
    review = [{"event": "reviewed", "user": {"login": "maint"}, "submitted_at": "2026-09-11T00:00:00Z"}]

    def fake_get(path, params=None, **kw):
        if path == "/search/issues":
            return {"total_count": 1 if "updated:<" in params["q"] else 4}
        if path == "/repos/o/r/pulls":
            return pulls[params["state"]]
        return review if path.endswith("/timeline") else responses[path]

    monkeypatch.setattr(rh, "api_get", fake_get)
    monkeypatch.setattr(rh, "raw_get", lambda *a: "")
    meta = {"default_branch": "main", "archived": False}
    result = rh.repo_health("o/r", meta, {".github/stale.yml"}, {}, now=NOW)
    assert result["verdict"] == "welcoming"
    assert result["outside_prs"] == 4 and result["last_commit_at"].startswith("2026-09-30")
    signals = {s["key"]: s for s in result["signals"]}
    assert signals["first_response"]["value"] == 1.0  # PR 2 is too new to sample
    assert signals["outside_merge_rate"]["detail"] == "67% of 3 recently closed"
    assert signals["stale_bot"]["value"] is True
