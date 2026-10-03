"""Web app: JSON endpoints and HTTP plumbing (GitHub and the model are faked)."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from buddy import web
from buddy.skill import GitHubError

GOOD_RANKING = {
    "ranked": [{"number": 2, "difficulty": "easy", "explanation": "Docs fix [#2]", "why": "tiny"}],
    "recommendation": {"number": 2, "reason": "Smallest [#2]"},
}


class FakeProvider:
    name, model = "fake", "fake-model"

    def __init__(self, reply):
        self.reply = reply

    def generate(self, system, prompt, json_mode=False):
        return self.reply


def issue(number, difficulty="medium"):
    return {"number": number, "title": f"Issue {number}", "url": f"https://github.com/o/r/issues/{number}",
            "labels": ["good first issue"], "age_days": 3, "comments": 0, "body": "Fix it.",
            "heuristic": {"difficulty": difficulty, "clarity": 50, "reasons": ["short"]}}


@pytest.fixture
def fake_issues(monkeypatch):
    found = {"repo": "o/r", "issues": [issue(1), issue(2)], "skipped": [], "labels_used": [],
             "note": None}
    monkeypatch.setattr(web.issues, "find_issues", lambda *a: found)
    monkeypatch.setattr(web.ranker, "rank_issues", lambda items: items)


def test_issues_uses_model_ranking(fake_issues, monkeypatch):
    monkeypatch.setattr(web, "get_provider", lambda: FakeProvider(json.dumps(GOOD_RANKING)))
    data = web.api_issues({"repo": "o/r"})
    assert [i["number"] for i in data["issues"]] == [2, 1]
    assert data["recommendation"]["number"] == 2
    assert data["ai_error"] is None and data["model"] == "fake-model"


def test_issues_falls_back_to_heuristics_without_a_model(fake_issues, monkeypatch):
    def no_key():
        raise web.ProviderError("GEMINI_API_KEY is not set.")

    monkeypatch.setattr(web, "get_provider", no_key)
    data = web.api_issues({"repo": "o/r"})
    assert [i["number"] for i in data["issues"]] == [1, 2]
    assert "GEMINI_API_KEY" in data["ai_error"] and data["recommendation"] is None


def test_issues_skips_model_when_ai_is_off(fake_issues, monkeypatch):
    monkeypatch.setattr(web, "get_provider", lambda: pytest.fail("model should not be built"))
    assert web.api_issues({"repo": "o/r", "ai": False})["ai_error"] is None


def test_rejects_bad_input():
    with pytest.raises(web.ApiError):
        web.api_issues({"repo": "o/r", "limit": 500})
    with pytest.raises(web.ApiError):
        web.api_plan({"issue_url": "  "})


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def call(url, body=None, content_type="application/json"):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def test_serves_the_page(server):
    status, body = call(server + "/")
    assert status == 200 and "<title>contrib-buddy</title>" in body


def test_github_errors_become_friendly_json(server, monkeypatch):
    def not_found(repo):
        raise GitHubError("Repository o/missing not found.")

    monkeypatch.setattr(web.repo_docs, "fetch_repo_docs", not_found)
    status, body = call(server + "/api/analyze", {"repo": "o/missing"})
    assert status == 400 and json.loads(body)["error"] == "Repository o/missing not found."


def test_requires_json_content_type(server):
    status, _ = call(server + "/api/analyze", {"repo": "o/r"}, content_type="text/plain")
    assert status == 415


def test_unknown_route_is_404(server):
    assert call(server + "/api/nope", {})[0] == 404
