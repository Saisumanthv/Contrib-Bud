#!/usr/bin/env python3
"""Pre-PR checklist, run inside your local clone. Never pushes or opens anything.

Usage:
    python scripts/precheck_pr.py [--base upstream/main] [--pr-body pr.md]
                                  [--skip-commands] [--timeout 600] [--markdown]

Checks: branch hygiene, uncommitted changes, diff size, commit message style,
DCO sign-off, CLA reminder, issue link, PR template completeness, changelog,
and runs the detected lint/test commands. Prints JSON (or Markdown) and exits
1 if any check fails.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from _github import GitHubError, emit_json, emit_text, run_cli
from fetch_repo_docs import STACK_FILES, detect_stack, extract_rules, find_doc, find_pr_templates

CONVENTIONAL_RE = re.compile(
    r"^(feat|fix|docs|style|refactor|perf|test|tests|build|ci|chore|revert)(\([^)]+\))?!?: \S"
)
ISSUE_REF_RE = re.compile(r"(#\d+|github\.com/[^/\s]+/[^/\s]+/issues/\d+)")
DIFF_WARN_LINES, DIFF_FAIL_LINES = 400, 1500
PY_MODULE_TOOLS = {"pytest", "ruff", "mypy", "black", "flake8", "tox", "nox", "pre-commit"}
ICON = {"pass": "✅", "warn": "⚠️", "fail": "❌", "skip": "⏭️"}


def check(name: str, status: str, detail: str) -> dict[str, str]:
    """Build one checklist entry."""
    return {"name": name, "status": status, "detail": detail}


def git(*args: str, cwd: Path | None = None) -> str:
    """Run a git command and return stdout; raise GitHubError on failure."""
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", check=True)
    except FileNotFoundError as exc:
        raise GitHubError("git is not installed or not on PATH.") from exc
    except subprocess.CalledProcessError as exc:
        raise GitHubError(f"git {' '.join(args)} failed: {exc.stderr.strip()[:200]}") from exc
    return out.stdout


# --- pure checks --------------------------------------------------------------

def check_commit_messages(messages: list[str], conventional: bool) -> dict[str, str]:
    """Check commit subjects for length, WIP/fixup markers and (optionally) Conventional Commits."""
    problems = []
    for msg in messages:
        subject = msg.splitlines()[0] if msg.strip() else ""
        if len(subject) > 72:
            problems.append(f"subject over 72 chars: '{subject[:50]}…'")
        if re.match(r"(?i)^(wip\b|fixup!|squash!)", subject):
            problems.append(f"WIP/fixup commit: '{subject}'")
        if conventional and not CONVENTIONAL_RE.match(subject):
            problems.append(f"not Conventional Commits: '{subject[:60]}'")
    if not problems:
        style = " (Conventional Commits)" if conventional else ""
        return check("Commit messages", "pass", f"{len(messages)} commit(s) look good{style}.")
    return check("Commit messages", "warn", "; ".join(problems[:5]))


def check_signoff(messages: list[str], required: bool) -> dict[str, str]:
    """Check every commit carries a DCO Signed-off-by trailer when required."""
    if not required:
        return check("DCO sign-off", "skip", "Not required by this repo's docs.")
    missing = sum("Signed-off-by:" not in m for m in messages)
    if missing:
        return check("DCO sign-off", "fail",
                     f"{missing} commit(s) lack 'Signed-off-by'. Fix: "
                     "git rebase --signoff <base> && git push --force-with-lease")
    return check("DCO sign-off", "pass", "All commits are signed off.")


def check_issue_link(pr_body: str | None, messages: list[str], required: bool) -> dict[str, str]:
    """Check the PR body (or commits) reference an issue."""
    where = pr_body if pr_body is not None else "\n".join(messages)
    if ISSUE_REF_RE.search(where):
        return check("Issue link", "pass", "An issue reference was found.")
    status = "fail" if required else "warn"
    return check("Issue link", status,
                 "No issue reference found. Add 'Fixes #<number>' to the PR description.")


def check_pr_body(pr_body: str | None, has_template: bool) -> dict[str, str]:
    """Check a PR description draft for unfilled template parts."""
    if pr_body is None:
        hint = "Pass --pr-body <file> to check your PR description"
        return check("PR template", "skip",
                     hint + (" against the repo's template." if has_template else "."))
    problems = []
    if re.search(r"<!--.*?-->", pr_body, re.DOTALL):
        problems.append("template comments (<!-- -->) still present")
    if re.search(r"(?im)^\s*(fix(es)?|close[sd]?|resolve[sd]?):?\s*#?\s*$", pr_body):
        problems.append("'Fixes #' has no issue number")
    empty = re.findall(r"(?m)^#{1,6}\s+(.+)\n\s*(?=^#{1,6}\s|\Z)", pr_body)
    if empty:
        problems.append("empty section(s): " + ", ".join(e.strip() for e in empty[:4]))
    unchecked = len(re.findall(r"- \[ \]", pr_body))
    if unchecked:
        problems.append(f"{unchecked} unchecked checkbox(es) — tick them or explain why not")
    if len(pr_body.strip()) < 40:
        problems.append("description is very short")
    if problems:
        return check("PR template", "warn", "; ".join(problems))
    return check("PR template", "pass", "PR description looks complete.")


def check_diff_size(files: int, added: int, removed: int) -> dict[str, str]:
    """Judge diff size; small, focused PRs get reviewed faster."""
    total = added + removed
    detail = f"{files} file(s), +{added}/-{removed}"
    if files == 0:
        return check("Diff size", "fail", "No changes compared to the base branch.")
    if total > DIFF_FAIL_LINES:
        return check("Diff size", "fail", detail + " — very large; split it into smaller PRs.")
    if total > DIFF_WARN_LINES:
        return check("Diff size", "warn", detail + " — large for a first PR; consider splitting.")
    return check("Diff size", "pass", detail)


def parse_shortstat(text: str) -> tuple[int, int, int]:
    """Parse `git diff --shortstat` output into (files, insertions, deletions)."""
    def num(pattern: str) -> int:
        m = re.search(pattern, text)
        return int(m.group(1)) if m else 0
    return num(r"(\d+) files? changed"), num(r"(\d+) insertions?"), num(r"(\d+) deletions?")


# --- environment-dependent checks ---------------------------------------------------

def resolve_base(base: str | None, root: Path) -> str:
    """Pick the comparison base: explicit, else upstream/origin default branch."""
    candidates = [base] if base else [
        "upstream/HEAD", "origin/HEAD", "upstream/main", "origin/main",
        "upstream/master", "origin/master", "main", "master",
    ]
    for ref in candidates:
        try:
            git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", cwd=root)
            return ref
        except GitHubError:
            continue
    raise GitHubError(f"Base branch {base or '(auto)'} not found. Run 'git fetch upstream' "
                      "and pass --base upstream/<default-branch>.")


def _not_found(proc: subprocess.CompletedProcess) -> bool:
    out = proc.stdout + proc.stderr
    return proc.returncode in (127, 9009) or "not recognized" in out or "command not found" in out


def run_command(cmd: str, root: Path, timeout: int) -> dict[str, str]:
    """Run one lint/test command and summarize the result.

    Python tools that are installed but not on PATH are retried as `python -m <tool>`.
    """
    def run(command: str) -> subprocess.CompletedProcess:
        return subprocess.run(command, cwd=root, shell=True, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)

    try:
        proc = run(cmd)
        if _not_found(proc) and cmd.split()[0] in PY_MODULE_TOOLS:
            proc = run(f'"{sys.executable}" -m {cmd}')
    except subprocess.TimeoutExpired:
        return check(f"Run `{cmd}`", "warn", f"timed out after {timeout}s")
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-6:]
    if proc.returncode == 0:
        return check(f"Run `{cmd}`", "pass", "exit 0")
    if _not_found(proc) or "No module named" in "\n".join(tail):
        return check(f"Run `{cmd}`", "warn", "tool not installed — install dev dependencies first")
    return check(f"Run `{cmd}`", "fail", f"exit {proc.returncode}: " + " | ".join(tail)[-400:])


def precheck(base: str | None, pr_body_path: str | None, run_cmds: bool,
             timeout: int) -> dict[str, Any]:
    """Run all checks in the current git repository."""
    try:
        root = Path(git("rev-parse", "--show-toplevel").strip())
    except GitHubError as exc:
        raise GitHubError("This is not a git repository. Run the check from inside your local "
                          "clone of the project (cd into it first).") from exc
    paths = set(git("ls-files", cwd=root).splitlines())

    def read(rel: str | None) -> str:
        if not rel:
            return ""
        try:
            return (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    stack_files = {p: read(p) for p in STACK_FILES if p in paths}
    stack_files.update({p: read(p) for p in paths if p.startswith(".github/workflows/")})
    stack = detect_stack(paths, stack_files)
    contributing = find_doc(paths, "CONTRIBUTING")
    templates = find_pr_templates(paths)
    rule_docs = {p: read(p) for p in [contributing, *templates[:1]] if p}
    rules = extract_rules(rule_docs, paths)

    base_ref = resolve_base(base, root)
    branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=root).strip()
    raw_log = git("log", f"{base_ref}..HEAD", "--format=%B%x00", cwd=root)
    messages = [m.strip() for m in raw_log.split("\x00") if m.strip()]
    pr_body = None
    if pr_body_path:
        try:
            pr_body = Path(pr_body_path).read_text(encoding="utf-8")
        except OSError as exc:
            raise GitHubError(f"Cannot read --pr-body file {pr_body_path}: {exc.strerror}") from exc

    checks = []
    if branch in ("main", "master", "develop", "trunk"):
        checks.append(check("Branch", "warn",
                            f"You are on '{branch}'. Create a feature branch for your PR."))
    else:
        checks.append(check("Branch", "pass", f"On feature branch '{branch}'."))
    dirty = git("status", "--porcelain", cwd=root).strip()
    checks.append(check("Working tree", "warn" if dirty else "pass",
                        "Uncommitted changes present — commit or stash them." if dirty
                        else "Clean."))
    if not messages:
        checks.append(check("Commits", "fail", f"No commits on this branch vs {base_ref}."))
    checks.append(check_diff_size(*parse_shortstat(
        git("diff", "--shortstat", f"{base_ref}...HEAD", cwd=root))))
    if messages:
        checks.append(check_commit_messages(messages, "conventional_commits" in rules))
        checks.append(check_signoff(messages, "dco" in rules))
    if "cla" in rules:
        checks.append(check("CLA", "warn", "This repo requires a CLA — sign it when the bot asks "
                            f"({rules['cla']['source']})."))
    checks.append(check_issue_link(pr_body, messages, "issue_link_required" in rules))
    checks.append(check_pr_body(pr_body, bool(templates)))
    if "changelog_required" in rules:
        changed = git("diff", "--name-only", f"{base_ref}...HEAD", cwd=root).lower()
        ok = "changelog" in changed or ".changeset/" in changed or "changes/" in changed
        checks.append(check("Changelog", "pass" if ok else "warn",
                            "Changelog entry included." if ok else
                            f"Docs mention a changelog ({rules['changelog_required']['source']}); "
                            "add an entry if needed."))

    commands = [c["command"] for c in stack["lint_commands"] + stack["test_commands"]]
    if not commands:
        checks.append(check("Lint/tests", "skip", "No lint/test commands detected."))
    elif not run_cmds:
        checks.append(check("Lint/tests", "skip", "Skipped (--skip-commands). Run manually: "
                            + "; ".join(commands)))
    else:
        checks += [run_command(c, root, timeout) for c in commands]

    summary = {s: sum(c["status"] == s for c in checks) for s in ("pass", "warn", "fail", "skip")}
    return {"repo_root": str(root), "branch": branch, "base": base_ref, "rules": sorted(rules),
            "checks": checks, "summary": summary, "ready": summary["fail"] == 0}


def to_markdown(result: dict[str, Any]) -> str:
    """Render the checklist as Markdown."""
    lines = [f"# Pre-PR checklist: `{result['branch']}` vs `{result['base']}`", ""]
    lines += [f"- {ICON[c['status']]} **{c['name']}** — {c['detail']}" for c in result["checks"]]
    s = result["summary"]
    verdict = "Ready to open a PR." if result["ready"] else "Fix the ❌ items before opening a PR."
    lines += ["", f"{s['pass']} passed, {s['warn']} warnings, {s['fail']} failed. {verdict}"]
    return "\n".join(lines)


def main() -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", help="base ref to compare against (default: auto-detect)")
    ap.add_argument("--pr-body", help="path to your PR description draft (Markdown)")
    ap.add_argument("--skip-commands", action="store_true", help="don't run lint/test commands")
    ap.add_argument("--timeout", type=int, default=600, help="per-command timeout in seconds")
    ap.add_argument("--markdown", action="store_true", help="print Markdown instead of JSON")
    args = ap.parse_args()
    result = precheck(args.base, args.pr_body, not args.skip_commands, args.timeout)
    emit_text(to_markdown(result)) if args.markdown else emit_json(result)
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    run_cli(main)
