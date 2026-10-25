#!/usr/bin/env python3
"""Exact git commands for a contribution, and a diagnosis of your clone's git state.

Usage:
    python scripts/git_helper.py --repo OWNER/REPO [--branch fix/12-x] [--default main]
    python scripts/git_helper.py --diagnose [--path .]     # inside your clone

Recipes cover forking and cloning, adding the upstream remote, starting a branch,
syncing your fork, updating a PR branch by rebasing, resolving conflicts, fixing
commits, signing off, squashing, pushing and undoing mistakes. Each step says what
the command does; steps that can lose work are marked.

--diagnose reads (never changes) your clone: remotes, whether origin is your fork,
the current branch, a rebase or merge in progress, commits made on the default
branch, unpushed or uncommitted work, and how far behind upstream you are. It
uses one GitHub API call to see whether origin is a fork. Prints JSON.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path
from typing import Any

from _github import GitHubError, api_get, emit_json, parse_repo, run_cli

REMOTE_RE = re.compile(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")
YOU = "YOUR-USERNAME"


def _step(cmd: str, why: str, danger: str = "") -> dict[str, str]:
    return {"cmd": cmd, "why": why, "danger": danger}


def recipes(repo: str, default: str = "main", branch: str | None = None,
            fork_owner: str | None = None) -> list[dict[str, Any]]:
    """Every recipe, with the repo, branches and your fork filled in."""
    owner, name = parse_repo(repo)
    you = fork_owner or YOU
    branch = branch or "my-change"
    up = f"upstream/{default}"
    push_again = _step("git push --force-with-lease",
                       "Update your PR. Rewriting your own PR branch is normal; --force-with-lease "
                       "refuses if someone else pushed to it meanwhile.")
    return [
        {"id": "setup", "title": "Fork and clone (once)", "when": "Before your first change.",
         "steps": [
             _step(f"https://github.com/{owner}/{name}/fork",
                   "Open this page and click 'Create fork': your own copy you can push to."),
             _step(f"git clone https://github.com/{you}/{name}.git",
                   "Download your fork. It becomes the remote called 'origin'."),
             _step(f"cd {name}", "Go into the project folder."),
             _step(f"git remote add upstream https://github.com/{owner}/{name}.git",
                   "Remember the original project as 'upstream', to get its latest changes."),
             _step("git remote -v",
                   f"Check: origin should be {you}/{name}, upstream {owner}/{name}."),
         ]},
        {"id": "branch", "title": "Start a branch for your change", "when": "For every new PR.",
         "steps": [
             _step("git fetch upstream", "Get the project's latest commits (changes nothing)."),
             _step(f"git switch -c {branch} {up}",
                   f"Create your branch from the latest {default}. Never work on {default}."),
         ]},
        {"id": "commit", "title": "Commit your work", "when": "After making changes.",
         "steps": [
             _step("git status", "See which files you changed."),
             _step("git add -p", "Choose the changes to include, piece by piece (y/n)."),
             _step('git commit -s -m "fix: short summary (#123)"',
                   "Save a commit. -s adds the 'Signed-off-by' line some projects require."),
         ]},
        {"id": "push", "title": "Push and open the PR", "when": "When your commits are ready.",
         "steps": [
             _step(f"git push -u origin {branch}", "Upload your branch to your fork."),
             _step(f"https://github.com/{owner}/{name}/compare/{default}...{you}:{branch}"
                   "?expand=1", "Open this page to create the pull request (you click the "
                   "button; nothing is opened for you)."),
         ]},
        {"id": "sync", "title": "Sync your fork's default branch", "when":
         f"When {default} on your fork is behind the project.",
         "steps": [
             _step(f"git switch {default}", f"Go to your {default}."),
             _step("git fetch upstream", "Get the project's latest commits."),
             _step(f"git merge --ff-only {up}",
                   "Move your branch forward. Fails safely if you committed to it by mistake "
                   "(see 'Undo mistakes')."),
             _step(f"git push origin {default}", "Update your fork on GitHub too."),
         ]},
        {"id": "update", "title": "Update your PR branch (rebase)", "when":
         "The PR is out of date, or a reviewer asks you to rebase.",
         "steps": [
             _step(f"git switch {branch}", "Go to your PR branch."),
             _step("git fetch upstream", "Get the project's latest commits."),
             _step(f"git rebase {up}",
                   "Replay your commits on top of the latest code. If it stops with conflicts, "
                   "see 'Resolve conflicts'."),
             push_again,
         ]},
        {"id": "conflicts", "title": "Resolve conflicts", "when":
         "git says CONFLICT during a rebase or merge.",
         "steps": [
             _step("git status", "Files marked 'both modified' have conflicts."),
             _step("(edit each file)",
                   "Keep the right code between <<<<<<< ======= >>>>>>> and delete the markers."),
             _step("git add <file>", "Mark each file as resolved."),
             _step("git rebase --continue", "Carry on (use 'git merge --continue' for a merge)."),
             _step("git rebase --abort", "Or give up and go back to how things were before.",
                   "Throws away the conflict fixes you made so far."),
         ]},
        {"id": "fix_commit", "title": "Fix your last commit", "when":
         "Wrong message, or you forgot a file.",
         "steps": [
             _step("git commit --amend", "Edit the last commit's message."),
             _step("git add <forgotten-file>", "Stage the forgotten change…"),
             _step("git commit --amend --no-edit", "…and add it to the last commit."),
             _step("git push --force-with-lease", "Only if you had already pushed it."),
         ]},
        {"id": "signoff", "title": "Sign off existing commits (DCO)", "when":
         "The DCO check fails: commits lack 'Signed-off-by'.",
         "steps": [
             _step("git fetch upstream", "Get the project's latest commits."),
             _step(f"git rebase --signoff {up}", "Add the sign-off line to each of your commits."),
             push_again,
         ]},
        {"id": "squash", "title": "Squash into one commit", "when":
         "A maintainer asks for a single commit.",
         "steps": [
             _step("git fetch upstream", "Get the project's latest commits."),
             _step(f"git reset --soft $(git merge-base HEAD {up})",
                   "Undo your commits but keep all their changes staged (works in bash and "
                   "PowerShell)."),
             _step('git commit -s -m "fix: short summary (#123)"', "Make one commit from them."),
             push_again,
         ]},
        {"id": "undo", "title": "Undo mistakes", "when": "Something went wrong.",
         "steps": [
             _step("git restore --staged <file>", "Unstage a file (keeps your edits)."),
             _step("git reset --soft HEAD~1", "Undo the last commit, keeping its changes."),
             _step("git restore <file>", "Throw away your edits to a file.",
                   "Your uncommitted edits to that file are gone for good."),
             _step(f"git branch {branch} && git reset --hard {up}",
                   f"Committed on {default} by accident: save the work on a new branch, then "
                   f"put {default} back to the project's version.",
                   f"Run on {default} only, after the branch command succeeded."),
             _step("git reflog", "Lost a commit? This lists everywhere HEAD has been; "
                   "`git switch -c rescue <hash>` gets it back."),
         ]},
    ]


# --- diagnosis of a local clone ------------------------------------------------------------

def _git(root: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def parse_remotes(text: str) -> dict[str, str]:
    """`git remote -v` -> {name: "owner/repo"} for GitHub remotes (fetch URLs)."""
    remotes = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and (m := REMOTE_RE.search(parts[1])):
            remotes.setdefault(parts[0], f"{m.group(1)}/{m.group(2)}")
    return remotes


def _finding(level: str, message: str, recipe: str | None = None,
             commands: list[str] | None = None) -> dict[str, Any]:
    return {"level": level, "message": message, "recipe": recipe, "commands": commands or []}


def _is_own(repo: str) -> bool:
    """Whether GITHUB_TOKEN belongs to the repo's owner (False without a token)."""
    try:
        login = api_get("/user").get("login", "")
    except GitHubError:  # no token (401) or offline
        return False
    return bool(login) and login.lower() == repo.split("/")[0].lower()


def diagnose(path: str = ".", check_fork: bool = True) -> dict[str, Any]:
    """Read a clone's git state and say what to fix first. Changes nothing."""
    root = Path(path).resolve()
    top = _git(root, "rev-parse", "--show-toplevel")
    if top is None:
        return {"state": {"is_repo": False}, "findings": [_finding(
            "error", "This folder isn't a git clone. Fork and clone the project first.",
            "setup")], "recipes": None}
    root = Path(top)
    git_dir = Path(_git(root, "rev-parse", "--absolute-git-dir") or root / ".git")
    remotes = parse_remotes(_git(root, "remote", "-v") or "")
    branch = _git(root, "symbolic-ref", "--short", "-q", "HEAD")
    findings: list[dict[str, Any]] = []
    origin, upstream = remotes.get("origin"), remotes.get("upstream")

    # Is origin the user's fork? GitHub knows: a fork has a parent. If it isn't a fork, it
    # may still be the user's own project: compare its owner with the token's user.
    # (`permissions.push` describes the token, and read-only tokens always say false.)
    parent = writable = None
    if origin and check_fork:
        try:
            meta = api_get(f"/repos/{origin}")
            parent = (meta.get("parent") or {}).get("full_name") if meta.get("fork") else None
            if not meta.get("fork") and not upstream:
                writable = _is_own(origin) or (meta.get("permissions") or {}).get("push") or None
                if not writable:
                    findings.append(_finding(
                        "warn", f"origin is {origin} itself, not a fork. Unless it's your own "
                        "project or you're a maintainer, you won't be able to push: fork it and "
                        "point origin at your fork.", "setup",
                        ["git remote rename origin upstream", "git remote add origin "
                         f"https://github.com/{YOU}/{origin.split('/')[1]}.git"]))
        except GitHubError:
            pass
    project = upstream or parent or origin
    own_project = writable is True and not parent  # working on your own repo: no fork needed
    if not upstream and (parent or origin) and not own_project \
            and not any(f["recipe"] == "setup" for f in findings):
        findings.append(_finding(
            "warn", "No 'upstream' remote: you can't get the project's latest changes.",
            "setup", [f"git remote add upstream https://github.com/{parent or '<OWNER>/<REPO>'}"
                      ".git"] if parent else ["git remote add upstream "
                                              "https://github.com/<OWNER>/<REPO>.git"]))

    head = _git(root, "symbolic-ref", "--short", "refs/remotes/upstream/HEAD") or ""
    default = head.removeprefix("upstream/") or next(
        (b for b in ("main", "master") if _git(root, "rev-parse", "--verify", "-q",
                                               f"refs/remotes/upstream/{b}")
         or _git(root, "rev-parse", "--verify", "-q", f"refs/heads/{b}")), "main")
    up = f"upstream/{default}" if upstream and _git(
        root, "rev-parse", "--verify", "-q", f"refs/remotes/upstream/{default}") else None

    if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
        findings.append(_finding("error", "A rebase is in progress. Resolve the conflicts and "
                                 "continue, or abort to go back.", "conflicts",
                                 ["git status", "git rebase --continue", "git rebase --abort"]))
    elif (git_dir / "MERGE_HEAD").exists():
        findings.append(_finding("error", "A merge is in progress.", "conflicts",
                                 ["git status", "git merge --continue", "git merge --abort"]))
    if branch is None:
        findings.append(_finding("warn", "You're on a detached HEAD (not on any branch). "
                                 "Create a branch before committing.", "branch",
                                 ["git switch -c my-change"]))

    dirty = _git(root, "status", "--porcelain") or ""
    changed = len(dirty.splitlines())
    ahead = behind = None
    if up:
        counts = _git(root, "rev-list", "--left-right", "--count", f"HEAD...{up}")
        if counts and len(counts.split()) == 2:
            ahead, behind = (int(n) for n in counts.split())
    if branch == default and ahead:
        findings.append(_finding(
            "warn", f"You have {ahead} commit(s) directly on {default}. Move them to a branch "
            f"so {default} can follow the project.", "undo",
            ["git branch my-change", f"git reset --hard {up}", "git switch my-change"]))
    elif branch == default and changed:
        findings.append(_finding("info", f"You're on {default} with uncommitted changes: "
                                 "start a branch before committing.", "branch",
                                 ["git switch -c my-change"]))
    if behind:
        findings.append(_finding(
            "info", f"Your branch is {behind} commit(s) behind {up} as of your last "
            "'git fetch upstream'.", "update" if branch != default else "sync"))
    if branch and branch != default:
        tracking = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        if not tracking:
            findings.append(_finding("info", f"Branch '{branch}' isn't pushed yet.", "push",
                                     [f"git push -u origin {branch}"]))
        elif (unpushed := _git(root, "rev-list", "--count", "@{u}..HEAD")) and unpushed != "0":
            findings.append(_finding("info", f"{unpushed} commit(s) not pushed yet.", "push",
                                     ["git push"]))
    if changed and branch != default:
        findings.append(_finding("info", f"{changed} file(s) changed but not committed.",
                                 "commit", ["git status", "git add -p", 'git commit -s -m "…"']))
    if not findings:
        findings.append(_finding("ok", "Your clone looks healthy."))

    state = {"is_repo": True, "root": str(root), "branch": branch, "default_branch": default,
             "remotes": remotes, "origin_is_fork_of": parent, "can_push_origin": writable,
             "uncommitted_files": changed,
             "ahead": ahead, "behind": behind}
    return {"state": state, "findings": findings,
            "recipes": recipes(project, default, branch if branch != default else None,
                               (origin or "").split("/")[0] or None) if project else None}


def main() -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", help="OWNER/REPO to write recipes for")
    ap.add_argument("--branch", help="your branch name")
    ap.add_argument("--default", default="main", help="the project's default branch")
    ap.add_argument("--diagnose", action="store_true", help="check the git state of a clone")
    ap.add_argument("--path", default=".", help="the clone to diagnose (default: here)")
    args = ap.parse_args()
    if args.diagnose:
        emit_json(diagnose(args.path))
    elif args.repo:
        emit_json(recipes(args.repo, args.default, args.branch))
    else:
        raise GitHubError("Give --repo OWNER/REPO for recipes, or --diagnose inside your clone.")
    return 0


if __name__ == "__main__":
    run_cli(main)
