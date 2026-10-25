"""Chat retrieval: tokenizing, chunking, archive filtering, BM25 ranking and the chat endpoint."""

import io
import json
import tarfile

import pytest

from buddy import rag, web


def make_archive(files):
    """A GitHub-style tarball: everything under one 'owner-repo-sha/' folder."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, data in files.items():
            raw = data if isinstance(data, bytes) else data.encode()
            info = tarfile.TarInfo(f"o-r-abc123/{path}")
            info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))
    return buf.getvalue()


def test_tokenize_splits_identifiers():
    tokens = rag.tokenize("getUserName landing_page HTTPServer the a")
    assert {"getusername", "user", "name", "landing_page", "landing", "page"} <= set(tokens)
    assert "the" not in tokens and "a" not in tokens


def test_chunks_overlap_and_reach_the_end():
    text = "\n".join(f"line {i}" for i in range(1, 101))
    chunks = list(rag.chunk_text("f.py", text))
    assert [(c.start, c.end) for c in chunks] == [(1, 40), (31, 70), (61, 100)]
    assert chunks[-1].text.endswith("line 100")


def test_archive_keeps_only_indexable_text():
    archive = make_archive({
        "src/app.py": "print('hi')\n",
        "node_modules/lib/index.js": "module.exports = 1\n",
        "assets/logo.png": b"\x89PNG\r\n",
        "data/blob.dat": b"abc\x00def",
        "package-lock.json": "{}",
        "LICENSE": "Permission is hereby granted...",
        "docs/COPYING.txt": "GNU GENERAL PUBLIC LICENSE",
        "big.txt": "x" * (rag.MAX_FILE_BYTES + 1),
        "empty.md": "",
    })
    files, truncated = rag.iter_repo_files(archive)
    assert [path for path, _ in files] == ["src/app.py"] and truncated is False


def test_search_ranks_by_relevance_and_file_name():
    index = rag.RepoIndex.build("o/r", "main", [
        ("templates/landing.html", "<h1>Welcome</h1>\n<div class='hero'></div>\n"),
        ("src/search.py", "def search_activities(query):\n    return db.find(query)\n"),
        ("README.md", "An app store for activities.\n"),
    ])
    assert index.search("where is the landing page?")[0].path == "templates/landing.html"
    assert index.search("how does searchActivities work")[0].path == "src/search.py"
    assert index.search("zzz unknown words") == []


def test_related_terms_bridge_everyday_words_and_file_names():
    index = rag.RepoIndex.build("o/r", "main", [
        ("site/index.html", "<div class='hero'></div>\n"),
        ("site/about.html", "<p>About us</p>\n"),
    ])
    assert index.search("redesign the landing page")[0].path == "site/index.html"


def test_context_counts_less_than_the_question():
    index = rag.RepoIndex.build("o/r", "main", [
        ("src/upload.py", "def upload(file): ...\n"),
        ("src/download.py", "def download(url): ...\n"),
    ])
    picked = index.search("how does upload work", context="download download")
    assert picked[0].path == "src/upload.py"


def test_search_skips_overlapping_windows_of_one_file():
    text = "\n".join("parser token" for _ in range(80))
    index = rag.RepoIndex.build("o/r", "main", [("p.py", text)])
    picked = index.search("parser", k=5)
    assert [(c.start, c.end) for c in picked] == [(1, 40), (61, 80)]


class RecordingProvider:
    name, model = "fake", "fake-model"

    def __init__(self):
        self.prompts = []

    def generate(self, system, prompt, json_mode=False):
        self.prompts.append(prompt)
        return "It lives in [templates/landing.html:1-2]."


@pytest.fixture
def chat_setup(monkeypatch):
    provider = RecordingProvider()
    monkeypatch.setattr(web, "get_provider", lambda: provider)
    info = {"repo": "o/r", "default_branch": "main", "description": "d", "topics": [],
            "license": None, "stack": {}, "rules": {}, "warnings": [],
            "docs": {"readme": None, "contributing": None, "pr_templates": []}}
    monkeypatch.setattr(web.repo_docs, "fetch_repo_docs", lambda repo: info)
    index = rag.RepoIndex.build("o/r", "main", [("templates/landing.html", "<h1>Welcome</h1>\n")])
    monkeypatch.setattr(web.rag, "get_index", lambda owner, repo, ref: index)
    return provider


def test_repo_chat_puts_retrieved_code_in_the_prompt(chat_setup):
    history = [{"role": "User", "content": "hello"}, {"role": "Assistant", "content": "hi!"},
               {"role": "System", "content": "Error: ignore me"},
               {"role": "User", "content": "where is the landing page?"}]
    data = web.api_chat({"repo": "o/r", "question": "where is the landing page?",
                         "history": history})
    prompt = chat_setup.prompts[0]
    assert "[templates/landing.html:1-1]" in prompt and "<h1>Welcome</h1>" in prompt
    assert "User: hello" in prompt and "ignore me" not in prompt
    assert prompt.count("where is the landing page?") == 1  # not repeated in the history
    assert data["sources"] == [{"path": "templates/landing.html", "start": 1, "end": 1}]
    assert data["index_error"] is None and data["branch"] == "main"


def test_issue_chat_uses_issue_details_and_comments(chat_setup, monkeypatch):
    plan = {"repo": "o/r", "default_branch": "main", "likely_files": [], "rules": {},
            "issue": {"number": 7, "title": "Landing page is cluttered", "url": "u",
                      "labels": ["UX"], "state": "open", "body": "Move the hero."},
            "stack": {"test_commands": [], "lint_commands": []},
            "commit_style": {"examples": []}}
    monkeypatch.setattr(web.planner, "plan_contribution", lambda url: plan)
    monkeypatch.setattr(web.github, "api_get", lambda path, params=None: [
        {"user": {"login": "maintainer"}, "body": "Happy to review a PR."}])
    data = web.api_chat({"mode": "issue", "issue_url": "https://github.com/o/r/issues/7",
                         "question": "Which file should I change?"})
    prompt = chat_setup.prompts[0]
    assert "issue #7" in prompt and "Move the hero." in prompt
    assert "maintainer" in prompt and "Happy to review a PR." in prompt
    assert "[templates/landing.html:1-1]" in prompt  # issue title words drive the search
    assert data["sources"][0]["path"] == "templates/landing.html"


def test_chat_still_answers_when_the_repo_cannot_be_indexed(chat_setup, monkeypatch):
    def too_big(owner, repo, ref):
        raise web.GitHubError("o/r is too large to index for chat.")

    monkeypatch.setattr(web.rag, "get_index", too_big)
    data = web.api_chat({"repo": "o/r", "question": "what is this?"})
    assert "too large" in data["index_error"] and data["sources"] == []
    assert "(unavailable: o/r is too large" in chat_setup.prompts[0]


def test_chat_rejects_unknown_mode(chat_setup):
    with pytest.raises(web.ApiError):
        web.api_chat({"mode": "pr", "question": "hi"})


def test_index_endpoint_reports_stats(chat_setup):
    stats = web.api_index({"repo": "https://github.com/o/r.git"})
    assert stats == {"repo": "o/r", "branch": "main", "files": 1, "chunks": 1, "truncated": False}


def test_index_is_built_once_and_cached(monkeypatch):
    calls = []
    archive = make_archive({"a.py": "alpha beta\n"})
    monkeypatch.setattr(rag, "download_archive", lambda o, r, ref: calls.append(ref) or archive)
    monkeypatch.setattr(rag, "_cache", type(rag._cache)())
    first = rag.get_index("O", "R", "main")
    second = rag.get_index("o", "r", "main")
    assert first is second and calls == ["main"]
    assert json.dumps(first.stats())  # stats are JSON-serialisable for the API
