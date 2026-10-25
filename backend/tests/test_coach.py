"""PR coach orchestration: code context, the model's answer, citations and the endpoint."""

import json

import pytest

from buddy import coach, llm, rag, web

CODE = "\n".join(f"line {i}" for i in range(1, 40)) + "\ndef parse(text):\n    return None\n"
INDEX = rag.RepoIndex.build("fork/r", "abc", [("src/parser.py", CODE), ("README.md", "Docs\n")])
FACTS = {
    "repo": "o/r",
    "pr": {"number": 5, "title": "Fix parser", "url": "u", "author": "me", "base": "main",
           "head": "fix/5", "head_sha": "abc", "head_repo": "fork/r", "mergeable_state": "clean"},
    "threads": [{"path": "src/parser.py", "line": 41, "outdated": False, "diff_hunk": "",
                 "url": "t", "waiting_on_you": True,
                 "comments": [{"author": "maint", "body": "Return {} here instead of None",
                               "at": "t", "by_pr_author": False}]}],
    "reviews": [{"reviewer": "maint", "state": "CHANGES_REQUESTED", "body": "", "at": "t",
                 "maintainer": True}],
    "conversation": [], "checks": [], "logs_need_token": False,
}
GOOD = {
    "summary": "One change was requested.",
    "items": [{"kind": "review", "title": "Return an empty dict",
               "what": "@maint wants `parse` to return `{}` [review by @maint].",
               "fix": "Change `return None` to `return {}` in [src/parser.py:40-41].",
               "commands": ["pytest -q"]}],
    "reply": "Thanks! Done.",
}


class FakeProvider:
    name, model = "fake", "fake-model"

    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    def generate(self, system, prompt, json_mode=False):
        self.prompts.append(prompt)
        return self.reply


def test_resolve_handles_ci_absolute_paths():
    assert coach.resolve(INDEX, "/home/runner/work/r/r/src/parser.py") == "src/parser.py"
    assert coach.resolve(INDEX, "src\\parser.py") == "src/parser.py"
    assert coach.resolve(INDEX, "/tmp/nope.py") is None


def test_code_context_takes_the_discussed_lines():
    (chunk, *_) = coach.code_context(INDEX, FACTS)
    assert chunk.path == "src/parser.py" and chunk.start <= 41 <= chunk.end
    assert "return None" in chunk.text


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(coach.coach, "coach_facts", lambda pr_url, text: json.loads(json.dumps(FACTS)))
    monkeypatch.setattr(coach.repo_docs, "fetch_repo_docs", lambda repo, **kw: {
        "repo": "o/r", "default_branch": "main", "rules": {"dco": {}},
        "stack": {"test_commands": [{"command": "pytest -q", "source": "pyproject.toml"}],
                  "lint_commands": []}})
    seen = {}

    def get_index(owner, repo, ref):
        seen["index"] = (owner, repo, ref)
        return INDEX

    monkeypatch.setattr(coach.rag, "get_index", get_index)
    return seen


def test_run_indexes_the_pr_head_and_checks_citations(setup):
    provider = FakeProvider(json.dumps(GOOD))
    result = coach.run("https://github.com/o/r/pull/5", None, None, provider)
    assert setup["index"] == ("fork", "r", "abc")  # the fork's head commit, not o/r main
    assert "[src/parser.py:" in provider.prompts[0] and "return None" in provider.prompts[0]
    item = result["ai"]["items"][0]
    assert item["citations"]["fix"][0]["status"] == "verified"
    assert result["guidance"][0]["kind"] == "review" and result["model"] == "fake-model"


def test_run_without_a_model_gives_rule_based_fixes(setup):
    result = coach.run("https://github.com/o/r/pull/5", None, None, None)
    assert result["ai"] is None and result["guidance"]
    assert result["sources"][0]["path"] == "src/parser.py"


def test_bad_model_output_falls_back(setup):
    result = coach.run("https://github.com/o/r/pull/5", None, None, FakeProvider("not json"))
    assert result["ai"] is None and "rule-based fixes" in result["ai_error"]


def test_validate_coach():
    assert llm.validate_coach(GOOD) == []
    assert llm.validate_coach({**GOOD, "items": []})
    bad_item = {**GOOD["items"][0], "kind": "vibes", "commands": "pytest"}
    errs = llm.validate_coach({**GOOD, "items": [bad_item]})
    assert any("kind" in e for e in errs) and any("commands" in e for e in errs)


def test_endpoint_validates_input(monkeypatch):
    with pytest.raises(web.ApiError):
        web.api_coach({"text": "  "})
    with pytest.raises(web.ApiError):
        web.api_coach({"pr_url": "https://github.com/o/r/issues/5"})


def test_endpoint_runs_pasted_logs_without_github(monkeypatch):
    monkeypatch.setattr(web, "get_provider", lambda: (_ for _ in ()).throw(
        web.ProviderError("no key")))
    data = web.api_coach({"text": "FAILED tests/t.py::test_x - KeyError: 'a'\nerror: boom\n"})
    assert data["guidance"][0]["kind"] == "test" and data["ai"] is None
    assert "no key" in data["ai_error"]
