"""Git helper: recipes, and diagnosing real (temporary) git clones."""

import subprocess
import tempfile
from pathlib import Path

import git_helper as gh
import pytest


def test_recipes_fill_in_repo_branch_and_fork():
    by_id = {r["id"]: r for r in gh.recipes("o/proj", "develop", "fix/12-crash", "me")}
    setup = [s["cmd"] for s in by_id["setup"]["steps"]]
    assert "git clone https://github.com/me/proj.git" in setup
    assert "git remote add upstream https://github.com/o/proj.git" in setup
    assert "git switch -c fix/12-crash upstream/develop" in [s["cmd"] for s in by_id["branch"]["steps"]]
    assert "git rebase upstream/develop" in [s["cmd"] for s in by_id["update"]["steps"]]
    push = by_id["push"]["steps"][1]["cmd"]
    assert push == "https://github.com/o/proj/compare/develop...me:fix/12-crash?expand=1"
    risky = [s for r in by_id.values() for s in r["steps"] if s["danger"]]
    assert {s["cmd"].split()[1] for s in risky} >= {"rebase", "restore"}  # abort, restore
    assert gh.recipes("o/proj")[0]["steps"][1]["cmd"].endswith(f"/{gh.YOU}/proj.git")


def test_parse_remotes_ssh_and_https():
    text = ("origin\tgit@github.com:me/proj.git (fetch)\norigin\tgit@github.com:me/proj.git (push)\n"
            "upstream\thttps://github.com/o/proj (fetch)\nother\thttps://gitlab.com/x/y (fetch)\n")
    assert gh.parse_remotes(text) == {"origin": "me/proj", "upstream": "o/proj"}


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": __import__("os").environ["PATH"],
                        "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")})


@pytest.fixture
def clone():
    """A 'project' repo and a clone of it, with upstream pointing at a GitHub URL."""
    base = Path(tempfile.mkdtemp(prefix="githelper-"))
    project, work = base / "project", base / "work"
    project.mkdir()
    git(project, "init", "-q", "-b", "main")
    (project / "a.txt").write_text("one\n")
    git(project, "add", ".")
    git(project, "commit", "-q", "-m", "init")
    git(base, "clone", "-q", str(project), "work")
    git(work, "remote", "rename", "origin", "upstream")
    git(work, "remote", "set-url", "upstream", "https://github.com/o/proj.git")
    git(work, "remote", "add", "origin", "https://github.com/me/proj.git")
    git(work, "update-ref", "refs/remotes/upstream/main", "HEAD")
    return work


def test_diagnose_commits_on_main_and_uncommitted_work(clone):
    (clone / "a.txt").write_text("two\n")
    git(clone, "commit", "-q", "-am", "oops on main")
    (clone / "b.txt").write_text("new\n")
    result = gh.diagnose(str(clone), check_fork=False)
    assert result["state"]["branch"] == "main" and result["state"]["ahead"] == 1
    assert any("directly on main" in f["message"] for f in result["findings"])
    assert result["recipes"][0]["steps"][1]["cmd"] == "git clone https://github.com/me/proj.git"


def test_diagnose_feature_branch_not_pushed(clone):
    git(clone, "switch", "-q", "-c", "fix/1")
    (clone / "a.txt").write_text("fix\n")
    git(clone, "commit", "-q", "-am", "fix")
    messages = [f["message"] for f in gh.diagnose(str(clone), check_fork=False)["findings"]]
    assert "Branch 'fix/1' isn't pushed yet." in messages


def test_diagnose_rebase_in_progress_and_missing_upstream(clone):
    (clone / ".git" / "rebase-merge").mkdir()
    findings = gh.diagnose(str(clone), check_fork=False)["findings"]
    assert findings[0]["level"] == "error" and "rebase is in progress" in findings[0]["message"]
    git(clone, "remote", "remove", "upstream")
    findings = gh.diagnose(str(clone), check_fork=False)["findings"]
    assert any("No 'upstream' remote" in f["message"] for f in findings)


def test_diagnose_cloned_the_original_instead_of_a_fork(clone, monkeypatch):
    git(clone, "remote", "remove", "origin")
    git(clone, "remote", "rename", "upstream", "origin")
    fake_github(monkeypatch, {"fork": False, "permissions": {"push": False}}, login="someone-else")
    (first, *_) = gh.diagnose(str(clone))["findings"]
    assert first["level"] == "warn" and "not a fork" in first["message"]
    assert first["commands"][0] == "git remote rename origin upstream"


def test_diagnose_outside_a_repo():
    result = gh.diagnose(tempfile.mkdtemp(prefix="notgit-"), check_fork=False)
    assert result["state"]["is_repo"] is False and result["findings"][0]["recipe"] == "setup"


def fake_github(monkeypatch, repo_meta, login=None):
    def api_get(path, **kw):
        if path == "/user":
            if login is None:
                raise gh.GitHubError("GITHUB_TOKEN was rejected (401).")
            return {"login": login}
        return repo_meta

    monkeypatch.setattr(gh, "api_get", api_get)


def test_own_project_needs_no_fork(clone, monkeypatch):
    git(clone, "remote", "remove", "origin")
    git(clone, "remote", "rename", "upstream", "origin")  # origin is now o/proj
    # A read-only token says push: false even for the owner, so the login decides.
    fake_github(monkeypatch, {"fork": False, "permissions": {"push": False}}, login="O")
    result = gh.diagnose(str(clone))
    assert result["state"]["can_push_origin"] is True
    assert not any(f["level"] in ("error", "warn") for f in result["findings"])
    fake_github(monkeypatch, {"fork": False}, login=None)  # no token: can't tell
    (first, *_) = gh.diagnose(str(clone))["findings"]
    assert first["level"] == "warn" and "Unless it's your own project" in first["message"]
