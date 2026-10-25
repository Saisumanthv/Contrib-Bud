#!/usr/bin/env python3
"""Gather the facts behind "my PR got review comments / CI failed: what now?".

Usage:
    python scripts/pr_coach.py https://github.com/OWNER/REPO/pull/45
    python scripts/pr_coach.py --text ci-log.txt          # or - for stdin
    python scripts/pr_coach.py https://github.com/OWNER/REPO/pull/45 --text review.txt

From a PR it collects open review threads (file, line, the diff around it, every
reply), reviews and their verdicts, conversation comments, merge conflicts and the
failing checks with their annotations and, when GITHUB_TOKEN allows, an excerpt
of the failed job's log. Pasted text (review comments or a CI log) is parsed too:
error lines are pulled out of logs and sorted into kinds (lint, format, test,
type, build, DCO, CLA, changelog, commit message). Each finding comes with a
concrete fix using the repo's own commands when they are known.
Prints JSON. Never posts, pushes or re-runs anything.
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Any

from _github import (
    API,
    CONNECT_TIMEOUT,
    GitHubError,
    _headers,
    _session,
    api_get,
    emit_json,
    parse_issue_url,
    run_cli,
)

MAX_TEXT = 400_000        # characters of pasted text or log read
MAX_EXCERPT = 8_000       # characters of log kept around the errors
CONTEXT_LINES = 3         # log lines kept before and after each error line
MAX_ERRORS = 30
MAX_CHECKS = 4            # failed checks whose annotations and logs are read
LOG_TAIL_BYTES = 1_500_000
FAILED = {"failure", "timed_out", "action_required", "startup_failure"}
MAINTAINER_ROLES = {"OWNER", "MEMBER", "COLLABORATOR"}

TIMESTAMP_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z ?")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PATH = r"[\w./@+-]+\.\w+"

# (kind, tool, pattern). Named groups: path, line, test, code, message.
LOG_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    ("test", "pytest", re.compile(
        r"^(?:FAILED|ERROR) (?P<path>" + PATH + r")(?:::(?P<test>[\w\[\]./:-]+))?"
        r"(?: - (?P<message>.*))?$")),
    ("type", "tsc", re.compile(
        r"^(?P<path>" + PATH + r")[(:](?P<line>\d+)[,:]\d+\)?:? -? ?error (?P<code>TS\d+): "
        r"(?P<message>.*)$")),
    ("type", "mypy", re.compile(
        r"^(?P<path>[\w./@+-]+\.pyi?):(?P<line>\d+): error: (?P<message>.*?)"
        r"(?:\s+\[(?P<code>[\w-]+)\])?$")),
    ("lint", "ruff/flake8", re.compile(
        r"^(?P<path>" + PATH + r"):(?P<line>\d+):\d+: (?P<code>[A-Z]+\d+) (?P<message>.*)$")),
    # ruff's full format and rustc print the location on a "  --> path:line:col" line below.
    ("lint", "ruff", re.compile(r"^(?P<code>[A-Z]{1,4}\d{3,4}) (?:\[\*\] )?(?P<message>.+)$")),
    ("lint", "eslint", re.compile(
        r"^\s+(?P<line>\d+):\d+\s+error\s+(?P<message>.*?)\s{2,}(?P<code>[\w/@-]+)$")),
    ("format", "black/ruff format", re.compile(
        r"^(?:would reformat|Would reformat:?) (?P<path>" + PATH + r")$", re.I)),
    ("format", "prettier", re.compile(r"^\[warn\] (?P<path>" + PATH + r")$")),
    ("test", "jest", re.compile(r"^\s*● (?P<test>.+ › .+)$")),
    ("test", "go test", re.compile(r"^\s*--- FAIL: (?P<test>\S+)")),
    ("build", "go", re.compile(r"^(?P<path>[\w./@+-]+\.go):(?P<line>\d+):(?:\d+:)? "
                               r"(?P<message>.*)$")),
    ("build", "cargo", re.compile(r"^error\[(?P<code>E\d{4})\]: (?P<message>.*)$")),
    ("build", "npm", re.compile(r"^npm ERR! (?P<message>.*)$")),
    ("build", "python", re.compile(
        r"^(?P<code>(?:ModuleNotFound|Import)Error): (?P<message>.*)$")),
    ("test", "python", re.compile(
        r"^(?P<code>\w+(?:\.\w+)*(?:Error|Exception)): (?P<message>.*)$")),
    ("dco", "DCO", re.compile(
        r"(?P<message>.*(?:\bDCO\b|sign-?off).*(?:missing|not found|fail|does not|required)"
        r".*|.*(?:missing|no|without) (?:a )?(?:Signed-off-by|sign-?off).*)$", re.I)),
    ("cla", "CLA", re.compile(
        r"(?P<message>.*(?:\bCLA\b|Contributor License Agreement).*(?:not (?:yet )?signed|"
        r"need(?:s)? to sign|please sign|required|missing).*)$", re.I)),
    ("changelog", "changeset", re.compile(
        r"(?P<message>.*(?:no changeset|changeset.*(?:missing|not found|required)|"
        r"changelog.*(?:missing|not updated|required)).*)$", re.I)),
    ("commit_message", "commitlint", re.compile(
        r"(?P<message>.*(?:subject may not be empty|type must be one of|"
        r"header must not be longer|commitlint).*)$", re.I)),
    ("ci", "GitHub Actions", re.compile(r"^(?:##\[error\]|::error[^:]*::)(?P<message>.*)$")),
    ("ci", "error", re.compile(r"^(?:error|fatal): (?P<message>.+)$", re.I)),  # git, pip, ...
]
TRACE_FILE_RE = re.compile(r'File "(?P<path>[^"]+)", line (?P<line>\d+)')
ARROW_RE = re.compile(r"^\s*-->\s*(?P<path>" + PATH + r"):(?P<line>\d+)(?::\d+)?\s*$")
PATH_ONLY_RE = re.compile(r"^(?P<path>/?" + PATH + r")$")  # eslint's per-file header
PYTEST_E_RE = re.compile(r"^E\s+(?P<message>.+)$")
KIND_ORDER = ["cla", "dco", "conflict", "commit_message", "changelog", "format", "lint", "type",
              "build", "test", "ci"]
SUGGESTION_RE = re.compile(r"```suggestion[^\n]*\n(.*?)```", re.S)
FILE_REF_RE = re.compile(r"(?<![\w/])(" + PATH + r")(?::(\d+))?")
NOT_PATHS = re.compile(r"^(e\.g|i\.e|etc|v?\d+(\.\d+)+)$|\.(com|org|io|net)$", re.I)


# --- pasted text ----------------------------------------------------------------------

def clean_log(text: str) -> list[str]:
    """Log lines without GitHub's timestamps and terminal colours."""
    return [TIMESTAMP_RE.sub("", ANSI_RE.sub("", line)).rstrip()
            for line in text[-MAX_TEXT:].splitlines()]


def parse_log(text: str) -> dict[str, Any]:
    """Error lines in a CI log, what kind each is, and an excerpt around them."""
    lines = clean_log(text)
    errors: list[dict[str, Any]] = []
    hits: list[int] = []
    seen = set()
    header = None  # the last line that was just a file path
    for i, line in enumerate(lines):
        if (arrow := ARROW_RE.match(line)) and errors and i - errors[-1]["line_no"] <= 3 \
                and "path" not in errors[-1]:
            errors[-1].update(arrow.groupdict())  # "error: ..." then "  --> src/x.rs:3:5"
            hits.append(i)
            continue
        if m := PATH_ONLY_RE.match(line.strip()):
            header = m["path"]
        for kind, tool, pattern in LOG_PATTERNS:
            m = pattern.search(line)
            if not m:
                continue
            found = {k: v for k, v in m.groupdict().items() if v}
            if tool == "eslint" and header:
                found["path"] = header
            if tool == "ruff/flake8" and found.get("message", "").startswith("[*]"):
                tool = "ruff"  # only ruff marks auto-fixable errors with [*]
            key = (kind, found.get("path"), found.get("line"), found.get("test"),
                   found.get("message", "")[:80])
            if key not in seen:
                seen.add(key)
                errors.append({"kind": kind, "tool": tool, "line_no": i + 1,
                               "text": line.strip()[:300], **found})
            hits.append(i)
            break
        else:
            if PYTEST_E_RE.match(line) or TRACE_FILE_RE.search(line):
                hits.append(i)
    # Python tracebacks: the last frame in the project's own code is the useful one.
    frames = [m.groupdict() for line in lines if (m := TRACE_FILE_RE.search(line))
              and "site-packages" not in m["path"] and not m["path"].startswith("<")]
    excerpt = _excerpt(lines, hits) if hits else "\n".join(lines[-60:])
    kinds = sorted({e["kind"] for e in errors}, key=KIND_ORDER.index)
    return {"errors": errors[:MAX_ERRORS], "kinds": kinds, "frames": frames[-5:],
            "excerpt": excerpt, "matched": bool(errors)}


def _excerpt(lines: list[str], hits: list[int]) -> str:
    """The lines around each hit, merged, with '…' where lines were skipped."""
    keep: set[int] = set()
    for i in hits:
        keep.update(range(max(0, i - CONTEXT_LINES), min(len(lines), i + CONTEXT_LINES + 1)))
    out, last = [], -2
    for i in sorted(keep):
        if i != last + 1:
            out.append("…")
        out.append(lines[i])
        last = i
    text = "\n".join(out)
    return text if len(text) <= MAX_EXCERPT else text[:MAX_EXCERPT // 2] + "\n…\n" + \
        text[-MAX_EXCERPT // 2:]


def looks_like_log(text: str) -> bool:
    """CI output rather than review prose: timestamps, error markers or many short lines."""
    sample = text[:20000]
    markers = len(re.findall(r"^\d{4}-\d\d-\d\dT\d\d:|##\[|^\s*(FAILED|ERROR|E\s{3}|npm ERR!)"
                             r"|Traceback \(most recent|exit code \d+|\berror(\[|:| TS)",
                             sample, re.M | re.I))
    return markers >= 2


def parse_review_text(text: str) -> dict[str, Any]:
    """What can be read mechanically from pasted review comments."""
    text = text[:MAX_TEXT]
    refs = []
    for m in FILE_REF_RE.finditer(re.sub(r"https?://\S+", " ", text)):
        path = m.group(1).strip(".")
        if "/" in path or re.search(r"\.(py|js|jsx|ts|tsx|go|rs|java|rb|md|rst|ya?ml|json|"
                                    r"toml|css|html)$", path):
            if not NOT_PATHS.search(path):
                refs.append({"path": path, "line": int(m.group(2)) if m.group(2) else None})
    unique = list({(r["path"], r["line"]): r for r in refs}.values())
    return {"suggestions": [s.strip("\n") for s in SUGGESTION_RE.findall(text)],
            "file_refs": unique[:15],
            "nits": len(re.findall(r"^\s*(?:[-*]\s*)?nit\b", text, re.I | re.M))}


# --- a PR on GitHub -------------------------------------------------------------------

def is_bot(user: dict[str, Any] | None) -> bool:
    user = user or {}
    return user.get("type") == "Bot" or str(user.get("login", "")).endswith("[bot]")


def review_threads(comments: list[dict[str, Any]], author: str | None) -> list[dict[str, Any]]:
    """Inline review comments grouped into threads (first comment plus its replies)."""
    threads: dict[int, dict[str, Any]] = {}
    for c in sorted(comments, key=lambda c: c.get("created_at") or ""):
        root = c.get("in_reply_to_id") or c["id"]
        if root not in threads:
            hunk = (c.get("diff_hunk") or "").splitlines()
            threads[root] = {
                "path": c.get("path"), "line": c.get("line") or c.get("original_line"),
                "outdated": c.get("line") is None, "diff_hunk": "\n".join(hunk[-8:]),
                "url": c.get("html_url"), "comments": []}
        login = (c.get("user") or {}).get("login")
        threads[root]["comments"].append({
            "author": login, "body": (c.get("body") or "")[:2000], "at": c.get("created_at"),
            "by_pr_author": login == author})
    out = list(threads.values())
    for t in out:
        t["waiting_on_you"] = not t["comments"][-1]["by_pr_author"]
    # Threads still waiting for the author first; outdated ones (code since changed) last.
    return sorted(out, key=lambda t: (not t["waiting_on_you"], t["outdated"]))


def latest_reviews(reviews: list[dict[str, Any]], author: str | None) -> list[dict[str, Any]]:
    """Each reviewer's latest verdict, with the review's own text."""
    latest: dict[str, dict[str, Any]] = {}
    for r in sorted(reviews, key=lambda r: r.get("submitted_at") or ""):
        who = (r.get("user") or {}).get("login")
        if not who or who == author or r.get("state") == "PENDING":
            continue
        if r.get("state") == "COMMENTED" and who in latest and not (r.get("body") or "").strip():
            continue  # a reply inside a thread doesn't change the reviewer's verdict
        latest[who] = {"reviewer": who, "state": r.get("state"),
                       "body": (r.get("body") or "")[:2000], "at": r.get("submitted_at"),
                       "maintainer": r.get("author_association") in MAINTAINER_ROLES}
    return sorted(latest.values(), key=lambda r: r["state"] != "CHANGES_REQUESTED")


def fetch_job_log(owner: str, repo: str, job_id: int) -> str | None:
    """A GitHub Actions job's log (its end, where failures are), or None if not allowed.

    GitHub only serves logs to authenticated requests, even for public repositories.
    """
    try:
        resp = _session().get(f"{API}/repos/{owner}/{repo}/actions/jobs/{job_id}/logs",
                              headers=_headers(), timeout=(CONNECT_TIMEOUT, 30), stream=True)
    except Exception:  # noqa: BLE001 - a missing log never breaks the coach
        return None
    with resp:
        if resp.status_code != 200:
            return None
        data = bytearray()
        for block in resp.iter_content(1 << 16):
            data += block
            if len(data) > 2 * LOG_TAIL_BYTES:
                del data[:len(data) - LOG_TAIL_BYTES]
    return bytes(data[-LOG_TAIL_BYTES:]).decode("utf-8", errors="replace")


def failed_checks(owner: str, repo: str, sha: str, logs: bool = True) -> list[dict[str, Any]]:
    """Failed check runs and commit statuses on the PR's head commit."""
    runs = api_get(f"/repos/{owner}/{repo}/commits/{sha}/check-runs", {"per_page": 100})
    out = []
    for run in [r for r in runs.get("check_runs", []) if r.get("conclusion") in FAILED]:
        check = {"name": run.get("name"), "conclusion": run.get("conclusion"),
                 "url": run.get("html_url"), "app": (run.get("app") or {}).get("slug"),
                 "summary": ((run.get("output") or {}).get("summary") or "")[:1500],
                 "annotations": [], "log": None}
        if len(out) < MAX_CHECKS:
            if (run.get("output") or {}).get("annotations_count"):
                check["annotations"] = [
                    {"path": a.get("path"), "line": a.get("start_line"), "level":
                     a.get("annotation_level"), "message": (a.get("message") or "")[:500]}
                    for a in api_get(f"/repos/{owner}/{repo}/check-runs/{run['id']}/annotations",
                                     {"per_page": 50})
                ][:20]
            if logs and check["app"] == "github-actions":
                text = fetch_job_log(owner, repo, run["id"])
                check["log"] = parse_log(text) if text else None
        out.append(check)
    status = api_get(f"/repos/{owner}/{repo}/commits/{sha}/status")
    for s in status.get("statuses", []):
        if s.get("state") in ("failure", "error"):
            out.append({"name": s.get("context"), "conclusion": s.get("state"),
                        "url": s.get("target_url"), "app": "status",
                        "summary": (s.get("description") or "")[:500], "annotations": [],
                        "log": None})
    return out


CHECK_NAME_WORDS = {
    "dco": ("dco", "signed-off", "sign-off"), "cla": ("cla", "license agreement"),
    "changelog": ("changeset", "changelog"), "lint": ("lint", "eslint", "ruff", "flake8"),
    "format": ("format", "prettier", "black"), "type": ("typecheck", "mypy", "tsc", "types"),
    "commit_message": ("commitlint", "semantic", "pr title"), "test": ("test", "pytest", "jest"),
}


def check_kinds(check: dict[str, Any]) -> list[str]:
    """What a failed check is about, from its log, else its name, summary and annotations."""
    kinds = set((check.get("log") or {}).get("kinds", [])) - {"ci"}
    if not kinds:
        text = " ".join([check.get("name") or "", check.get("summary") or ""] +
                        [a["message"] for a in check.get("annotations", [])]).lower()
        kinds = {kind for kind, words in CHECK_NAME_WORDS.items()
                 if any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)}
    return sorted(kinds or {"ci"}, key=KIND_ORDER.index)


def fetch_pr_feedback(pr_url: str, logs: bool = True) -> dict[str, Any]:
    """Everything reviewers and CI have said about a PR. About 6-10 API calls."""
    owner, repo, number = parse_issue_url(pr_url)
    pr = api_get(f"/repos/{owner}/{repo}/pulls/{number}",
                 not_found=f"Pull request #{number} not found in {owner}/{repo}.")
    author = (pr.get("user") or {}).get("login")
    comments = api_get(f"/repos/{owner}/{repo}/pulls/{number}/comments", {"per_page": 100})
    reviews = api_get(f"/repos/{owner}/{repo}/pulls/{number}/reviews", {"per_page": 100})
    talk = api_get(f"/repos/{owner}/{repo}/issues/{number}/comments", {"per_page": 100})
    checks = failed_checks(owner, repo, pr["head"]["sha"], logs) if pr.get("state") == "open" \
        else []
    for check in checks:
        check["kinds"] = check_kinds(check)
    return {
        "repo": pr["base"]["repo"]["full_name"],
        "pr": {"number": number, "title": pr.get("title"), "url": pr.get("html_url"),
               "author": author, "state": pr.get("state"), "merged": bool(pr.get("merged")),
               "draft": bool(pr.get("draft")), "base": pr["base"]["ref"],
               "head": pr["head"]["ref"], "head_sha": pr["head"]["sha"],
               # The fork the PR comes from (None if it was deleted): reviews are about its code.
               "head_repo": (pr["head"].get("repo") or {}).get("full_name"),
               "mergeable_state": pr.get("mergeable_state"),
               "commits": pr.get("commits"), "body": (pr.get("body") or "")[:2000]},
        "threads": review_threads(comments, author),
        "reviews": latest_reviews(reviews, author),
        "conversation": [
            {"author": (c.get("user") or {}).get("login"), "bot": is_bot(c.get("user")),
             "body": (c.get("body") or "")[:1500], "at": c.get("created_at")}
            for c in talk if (c.get("user") or {}).get("login") != author][-12:],
        "checks": checks,
        "logs_need_token": logs and any(c["app"] == "github-actions" and c["log"] is None
                                        for c in checks[:MAX_CHECKS]),
    }


# --- concrete fixes ---------------------------------------------------------------------

def _tool_command(kind: str, errors: list[dict[str, Any]], stack: dict[str, Any]) -> list[str]:
    """Commands that fix or reproduce one kind of failure: the tool's own fixer for the
    files it named, then the repo's documented command to check again."""
    mine = [e for e in errors if e["kind"] == kind]
    tools = {e["tool"] for e in mine}
    files = " ".join(sorted({e["path"] for e in mine if e.get("path")})[:5]) or "."
    lint_cmds = [c["command"] for c in stack.get("lint_commands", [])][:2]
    uses_ruff = any("ruff" in c for c in lint_cmds)
    if kind == "lint":
        fixer = (["npx eslint --fix " + files] if "eslint" in tools
                 else ["ruff check --fix " + files] if tools & {"ruff"} or (
                     "ruff/flake8" in tools and uses_ruff) else [])
        return list(dict.fromkeys(fixer + lint_cmds))
    if kind == "format":
        fixer = (["npx prettier --write " + files] if "prettier" in tools
                 else ["ruff format " + files] if uses_ruff
                 else ["black " + files] if "black/ruff format" in tools else [])
        return list(dict.fromkeys(fixer + lint_cmds))
    if kind == "type":
        return (["npx tsc --noEmit"] if "tsc" in tools else ["mypy " + files] if "mypy" in tools
                else [])
    if kind == "test":
        tests = [e for e in errors if e["kind"] == "test" and e.get("test")]
        if tests and tests[0]["tool"] == "pytest":
            return [f"pytest {tests[0]['path']}::{tests[0]['test']} -x"]
        if tests and tests[0]["tool"] == "jest":
            return [f"npx jest -t \"{tests[0]['test'].split(' › ')[-1]}\""]
        if tests and tests[0]["tool"] == "go test":
            return [f"go test ./... -run '^{tests[0]['test']}$'"]
        return [c["command"] for c in stack.get("test_commands", [])][:1]
    if kind == "build":
        return [c["command"] for c in stack.get("install_commands", [])][:1]
    if kind == "changelog" and any("changeset" in e["text"].lower() for e in mine):
        return ["npx changeset"]  # the Changesets tool writes the entry for you
    return []


ADVICE: dict[str, tuple[str, str, list[str]]] = {
    "dco": ("Sign off your commits (DCO)",
            "The project needs every commit to carry a `Signed-off-by:` line. Add it to your "
            "commits and update the PR. Force-pushing your own PR branch is expected here.",
            ["git rebase --signoff {base}", "git push --force-with-lease"]),
    "cla": ("Sign the Contributor License Agreement",
            "This can't be fixed in code: follow the link in the CLA bot's comment or check and "
            "sign it with the same email address as your commits. The check re-runs on its own.",
            []),
    "changelog": ("Add a changelog entry",
                  "The project wants each PR to describe its change for the release notes. "
                  "See CONTRIBUTING for where the entry goes.", []),
    "commit_message": ("Reword the commit message or PR title",
                       "A check enforces a message format (often Conventional Commits, like "
                       "`fix: handle empty config`). Match the format shown in the error.",
                       ["git commit --amend", "git push --force-with-lease"]),
    "conflict": ("Resolve merge conflicts",
                 "Your branch and the base branch changed the same lines. Bring your branch up "
                 "to date, fix the conflicts, then update the PR.",
                 ["git fetch upstream", "git rebase upstream/{base}",
                  "git push --force-with-lease"]),
    "format": ("Run the formatter", "Some files aren't formatted the way the project's "
               "formatter wants. Run it and commit the result; no logic changes needed.", []),
    "lint": ("Fix the lint errors", "The linter found style or correctness problems. Many can "
             "be fixed automatically; fix the rest by hand at the lines it names.", []),
    "type": ("Fix the type errors", "The type checker found code whose types don't line up. "
             "Fix them at the lines it names, then run it again.", []),
    "test": ("Make the failing test pass", "Run the failing test locally, read the assertion "
             "or exception, and decide whether your change or the test needs updating.", []),
    "build": ("Fix the build", "The project didn't install or compile. Reproduce it locally "
              "with a clean install and read the first error, not the last.", []),
    "ci": ("Read the failing check", "Open the check's log and look for the first error line.",
           []),
}


def guidance(facts: dict[str, Any], stack: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Plain fixes for each kind of failure found, without a model."""
    stack = stack or {}
    pr = facts.get("pr") or {}
    base = pr.get("base") or "main"
    errors = list((facts.get("pasted") or {}).get("log", {}).get("errors", []))
    kinds: dict[str, list[str]] = {}
    for e in errors:
        kinds.setdefault(e["kind"], []).append("your pasted log")
    for check in facts.get("checks", []):
        errors += (check.get("log") or {}).get("errors", [])
        for kind in check.get("kinds", []):
            kinds.setdefault(kind, []).append(f"check \"{check['name']}\"")
    if pr.get("mergeable_state") == "dirty":
        kinds["conflict"] = ["GitHub (merge conflicts)"]
    if len(kinds) > 1:  # "Process completed with exit code 1" adds nothing to a known cause
        kinds.pop("ci", None)
    out = []
    for kind in sorted(kinds, key=KIND_ORDER.index):
        title, explanation, commands = ADVICE[kind]
        cmds = [c.format(base=base) for c in commands] or _tool_command(kind, errors, stack)
        where = sorted({f"`{e['path']}:{e['line']}`" if e.get("line") else f"`{e['path']}`"
                        for e in errors if e["kind"] == kind and e.get("path")})[:5]
        out.append({"kind": kind, "title": title, "explanation": explanation,
                    "where": where, "commands": cmds, "source": ", ".join(dict.fromkeys(
                        kinds[kind]))})
    waiting = [t for t in facts.get("threads", []) if t["waiting_on_you"] and not t["outdated"]]
    if waiting:
        out.append({"kind": "review", "title": f"Reply to {len(waiting)} review thread(s)",
                    "explanation": "Reviewers are waiting on these. Change the code or explain "
                    "why not, then reply in each thread so they know it's handled.",
                    "where": [f"`{t['path']}:{t['line']}`" for t in waiting[:5]], "commands": [],
                    "source": "review comments"})
    return out


def coach_facts(pr_url: str | None = None, text: str | None = None,
                logs: bool = True) -> dict[str, Any]:
    """The PR's feedback and/or the pasted text, parsed. At least one is needed."""
    if not pr_url and not (text and text.strip()):
        raise GitHubError("Paste review comments or a CI log, or give a pull request link.")
    facts: dict[str, Any] = fetch_pr_feedback(pr_url, logs) if pr_url else {
        "repo": None, "pr": None, "threads": [], "reviews": [], "conversation": [],
        "checks": [], "logs_need_token": False}
    if text and text.strip():
        # Both parsers run: a review can quote an error, and a short paste may be one error.
        log = parse_log(text)
        kind = "log" if looks_like_log(text) or log["matched"] else "review"
        facts["pasted"] = {"kind": kind, "text": text[:12000], "log": log,
                           "review": parse_review_text(text)}
    return facts


def main() -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pr_url", nargs="?", help="https://github.com/OWNER/REPO/pull/N")
    ap.add_argument("--text", help="file with pasted review comments or a CI log (- for stdin)")
    ap.add_argument("--no-logs", action="store_true", help="don't download failed job logs")
    args = ap.parse_args()
    text = None
    if args.text:
        text = sys.stdin.read() if args.text == "-" else open(args.text, encoding="utf-8",
                                                              errors="replace").read()
    facts = coach_facts(args.pr_url, text, not args.no_logs)
    emit_json({**facts, "guidance": guidance(facts)})
    return 0


if __name__ == "__main__":
    run_cli(main)
