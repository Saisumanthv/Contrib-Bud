#!/usr/bin/env python3
"""Generate a copy-pasteable contribution plan for one GitHub issue.

Usage:
    python scripts/plan_contribution.py https://github.com/OWNER/REPO/issues/123 [--json]

Combines the issue, the repo's docs/stack/rules (fetch_repo_docs.py) and the
recent commit history to produce: fork/clone/branch commands, setup and test
commands, contribution rules, likely files to touch (keyword search over the
repo tree), a commit message in the project's style, and a PR description
built from the repo's PR template. Prints Markdown (or JSON with --json).
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from string import Template
from typing import Any

from _github import GitHubError, api_get, emit_json, emit_text, parse_issue_url, run_cli
from fetch_repo_docs import fetch_repo_docs, fetch_tree

ASSETS = Path(__file__).resolve().parent.parent / "assets"

CONVENTIONAL_RE = re.compile(
    r"^(feat|fix|docs|style|refactor|perf|test|tests|build|ci|chore|revert)(\([^)]+\))?!?: \S"
)
LABEL_TYPES: tuple[tuple[str, str], ...] = (
    (r"\bdocs?\b|documentation|typo", "docs"),
    (r"\btests?\b|testing|coverage", "test"),
    (r"bug|defect|regression|crash|error", "fix"),
    (r"feature|enhancement|feat\b|improvement", "feat"),
    (r"refactor|cleanup|tech[\s_-]*debt", "refactor"),
    (r"\bci\b|build|infra", "ci"),
)
BRANCH_ALIASES = {"feat": ("feat", "feature"), "fix": ("fix", "bugfix"),
                  "docs": ("docs",), "test": ("test",), "refactor": ("refactor",), "ci": ("ci",)}
STOPWORDS = set(
    "the a an and or but if then else when while for to of in on at by with from into onto "
    "is are was were be been being this that these those it its as not no can could should "
    "would will shall may might must do does did done have has had add adds added use uses "
    "used using make makes made fix fixes fixed issue issues bug bugs error errors new old "
    "support allow also only just like more most some any all each other than so very there "
    "here what which who whom how why where we you they i me my our your their them us get "
    "set out up down over under after before about via per etc feature request docs test "
    "tests example examples file files code line lines value values type types work works "
    "need needs want wants see seems seem show shows instead currently still already".split()
)
SKIP_DIRS = re.compile(r"(^|/)(node_modules|vendor|dist|build|third_party|\.git|__snapshots__)/")
LOCKFILES = re.compile(r"(lock\.json|\.lock|-lock\.yaml|\.min\.js|\.map)$")
# "Fixes #", "Fixes: ", "Closes #<issue number>", "- Resolves: ___" with no real number yet
ISSUE_PLACEHOLDER_RE = re.compile(
    r"^([ \t]*(?:[-*][ \t]*)?\**(?:Fix(?:es)?|Close[sd]?|Resolve[sd]?)\**:?\**)"
    r"[ \t]*(?:#[ \t]*)?(?:<[^>\n]*>|_+|X+|\.\.\.|\(.*?\))?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
COMMON_KEYWORD_SHARE = 0.03  # ignore keywords matching more than 3% of paths (e.g. "lib", "src")


# --- pure helpers -------------------------------------------------------------

def change_type(labels: list[str], title: str) -> str:
    """Infer a conventional change type (fix/feat/docs/...) from labels, then the title."""
    for text in (" | ".join(labels), title):
        for pattern, ctype in LABEL_TYPES:
            if re.search(pattern, text, re.IGNORECASE):
                return ctype
    return "fix"


def slugify(text: str, max_words: int = 6, max_len: int = 40) -> str:
    """Lowercase, hyphenated slug from the first meaningful words of `text`."""
    text = re.sub(r"^\s*(\[[^\]]*\]|\w+(\([^)]*\))?:)\s*", "", text)  # drop "[Bug]" / "fix:"
    words = re.findall(r"[a-z0-9]+", text.lower())
    slug = "-".join(words[:max_words])[:max_len].strip("-")
    return slug or "change"


def make_branch_name(
    number: int, title: str, labels: list[str], prefixes: list[str] | None = None
) -> str:
    """Build a branch name like 'fix/123-short-title'.

    Uses the repo's documented prefix style when known (e.g. 'feature/' vs 'feat/').
    """
    ctype = change_type(labels, title)
    prefix = ctype
    if prefixes:
        prefix = next((p for p in BRANCH_ALIASES.get(ctype, (ctype,)) if p in prefixes), ctype)
    return f"{prefix}/{number}-{slugify(title)}"


def detect_commit_style(messages: list[str]) -> dict[str, Any]:
    """Summarize commit conventions from recent commit messages."""
    subjects = [m.splitlines()[0] for m in messages if m.strip()]
    if not subjects:
        return {"conventional": False, "conventional_ratio": 0.0, "signoff_ratio": 0.0,
                "issue_ref_ratio": 0.0, "examples": []}
    n = len(subjects)
    conv = sum(bool(CONVENTIONAL_RE.match(s)) for s in subjects) / n
    signoff = sum("Signed-off-by:" in m for m in messages) / len(messages)
    refs = sum(bool(re.search(r"#\d+", s)) for s in subjects) / n
    return {
        "conventional": conv >= 0.5,
        "conventional_ratio": round(conv, 2),
        "signoff_ratio": round(signoff, 2),
        "issue_ref_ratio": round(refs, 2),
        "examples": subjects[:5],
    }


def make_commit_message(number: int, title: str, labels: list[str], style: dict[str, Any]) -> str:
    """Draft a commit subject in the project's style (<= ~72 chars)."""
    clean = re.sub(r"^\s*(\[[^\]]*\]|\w+(\([^)]*\))?:)\s*", "", title).strip().rstrip(".")
    suffix = f" (#{number})"
    if style.get("conventional"):
        summary = clean[:1].lower() + clean[1:]
        head = f"{change_type(labels, title)}: "
        return head + summary[: 72 - len(head) - len(suffix)].rstrip() + suffix
    summary = clean[:1].upper() + clean[1:]
    return summary[: 72 - len(suffix)].rstrip() + suffix


def extract_keywords(text: str) -> dict[str, int]:
    """Pull search keywords from issue text, weighted by how specific they are."""
    weights: dict[str, int] = {}

    def bump(word: str, w: int) -> None:
        word = word.strip("./`'\"()[]{}<>,:;").lower()
        if len(word) >= 3 and word not in STOPWORDS and not word.isdigit():
            weights[word] = max(weights.get(word, 0), w)

    for m in re.finditer(r"[\w./-]+\.[a-z]{1,5}\b", text):  # explicit file names / paths
        bump(m.group(0), 10)
    for m in re.finditer(r"`([^`\n]{2,60})`", text):  # inline code
        for part in re.split(r"[^\w-]+", m.group(1)):
            bump(part, 4)
    for m in re.finditer(r"\b([a-z]+[A-Z]\w*|[A-Z][a-z]+[A-Z]\w*|\w+_\w+)\b", text):  # identifiers
        bump(m.group(1), 3)
    for m in re.finditer(r"\b[a-zA-Z]{4,}\b", text):
        bump(m.group(0), 1)
    return weights


def find_likely_files(text: str, paths: set[str], limit: int = 8) -> list[dict[str, Any]]:
    """Rank repo paths by keyword overlap with the issue text."""
    keywords = extract_keywords(text)
    if not keywords:
        return []
    candidates = []
    for path in paths:
        if SKIP_DIRS.search(path) or LOCKFILES.search(path):
            continue
        lower = path.lower()
        base = lower.rsplit("/", 1)[-1]
        stem_tokens = set(re.split(r"[^a-z0-9]+", base.rsplit(".", 1)[0]))
        dir_part = lower.rsplit("/", 1)[0] if "/" in lower else ""
        dir_tokens = set(re.split(r"[^a-z0-9]+", dir_part)) - {""}
        candidates.append((path, lower, base, stem_tokens, dir_tokens))

    # Drop plain-word keywords so common they carry no signal (IDF-style).
    max_hits = max(5, int(len(candidates) * COMMON_KEYWORD_SHARE))
    for kw in [k for k in keywords if "/" not in k and "." not in k]:
        hits = sum(kw in st or kw in dt for _, _, _, st, dt in candidates)
        if hits > max_hits:
            del keywords[kw]

    scored = []
    for path, lower, base, stem_tokens, dir_tokens in candidates:
        score, hits = 0, []
        for kw, w in keywords.items():
            if "/" in kw or "." in kw:
                if lower.endswith(kw) or base == kw.rsplit("/", 1)[-1]:
                    score += w * 3
                    hits.append(kw)
            elif kw in stem_tokens or base.startswith(kw + "."):
                score += w * 2
                hits.append(kw)
            elif kw in dir_tokens:
                score += w
                hits.append(kw)
        if score >= 3:
            is_test = bool(re.search(r"(^|/)(tests?|__tests__|spec)/|[._-](test|spec)\.", lower))
            scored.append((score - (1 if is_test else 0), path, hits))
    scored.sort(key=lambda s: (-s[0], len(s[1])))
    return [{"path": p, "score": s, "matched": sorted(set(h))[:5]} for s, p, h in scored[:limit]]


def fill_pr_template(template: str | None, number: int, title: str, test_cmd: str) -> str:
    """Return a PR description: the repo's template with the issue linked, or our default."""
    if template:
        body = re.sub(r"<!--.*?-->", "", template, flags=re.DOTALL)  # drop author instructions
        body = ISSUE_PLACEHOLDER_RE.sub(lambda m: f"{m.group(1)} #{number}", body)
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        if f"#{number}" not in body:
            body = f"Fixes #{number}\n\n{body}"
        return body
    default = (ASSETS / "pr-description-template.md").read_text(encoding="utf-8")
    return Template(default).safe_substitute(
        issue_number=number, issue_title=title, test_command=test_cmd or "the test suite",
        summary="<!-- Describe your change in one or two sentences. -->",
    ).strip()


def format_rules(rules: dict[str, Any]) -> str:
    """Render detected contribution rules as a Markdown bullet list with sources."""
    labels = {
        "dco": "Sign off every commit (DCO): use `git commit -s`",
        "cla": "Sign the Contributor License Agreement (CLA) before your PR can be merged",
        "issue_link_required": "Link the issue in your PR (e.g. `Fixes #N`)",
        "conventional_commits": "Use Conventional Commits (`type(scope): summary`)",
        "changelog_required": "Changelog / changeset entry may be required",
        "tests_required": "Add or update tests for your change",
        "ai_policy": "The project has guidance on AI-assisted contributions — read it",
        "no_assignment_needed": "No need to be assigned before opening a PR",
        "assignment_required": "Ask to be assigned before starting work",
    }
    lines = []
    for key, text in labels.items():
        if key in rules:
            r = rules[key]
            lines.append(f"- {text} — _{r['source']}: \"{r['evidence'][:120]}\"_")
    if not lines:
        lines.append("- No specific rules detected automatically — read CONTRIBUTING/README.")
    return "\n".join(lines)


def _code_lines(items: list[dict[str, str]], fallback: str) -> str:
    if not items:
        return f"# {fallback}"
    return "\n".join(f"{i['command']}   # from {i['source']}" for i in items)


# --- orchestration ----------------------------------------------------------

def plan_contribution(issue_url: str) -> dict[str, Any]:
    """Collect facts and render the plan. Returns a dict including 'markdown'."""
    owner, repo, number = parse_issue_url(issue_url)
    issue = api_get(f"/repos/{owner}/{repo}/issues/{number}",
                    not_found=f"Issue #{number} not found in {owner}/{repo}.")
    warnings = []
    if "pull_request" in issue:
        raise GitHubError(f"{issue_url} is a pull request, not an issue.")
    if issue.get("state") != "open":
        warnings.append("This issue is closed — pick an open one.")
    if issue.get("assignees"):
        who = ", ".join("@" + a["login"] for a in issue["assignees"])
        warnings.append(f"This issue is assigned to {who}. Ask before working on it.")

    repo_info = fetch_repo_docs(f"{owner}/{repo}")
    owner, repo = repo_info["repo"].split("/")
    branch_default = repo_info["default_branch"]
    paths, _ = fetch_tree(owner, repo, branch_default)
    commits = api_get(f"/repos/{owner}/{repo}/commits", {"per_page": 30})
    style = detect_commit_style([c["commit"]["message"] for c in commits])

    labels = [lbl["name"] for lbl in issue.get("labels", [])]
    title = issue.get("title", "")
    rules = repo_info["rules"]
    prefixes = (rules.get("branch_prefixes") or {}).get("value")
    branch = make_branch_name(number, title, labels, prefixes)
    if rules.get("conventional_commits"):
        style["conventional"] = True
    commit_message = make_commit_message(number, title, labels, style)
    dco = "dco" in rules or style["signoff_ratio"] >= 0.5
    stack = repo_info["stack"]
    test_cmd = stack["test_commands"][0]["command"] if stack["test_commands"] else ""
    templates = repo_info["docs"]["pr_templates"]
    pr_template = templates[0]["content"] if templates else None
    pr_description = fill_pr_template(pr_template, number, title, test_cmd)
    likely = find_likely_files(f"{title}\n{issue.get('body') or ''}", paths)

    data = {
        "repo": repo_info["repo"],
        "issue": {"number": number, "title": title, "url": issue["html_url"], "labels": labels,
                  "state": issue.get("state"), "body": (issue.get("body") or "")[:4000]},
        "default_branch": branch_default,
        "branch": branch,
        "commit_message": commit_message,
        "commit_style": style,
        "dco": dco,
        "stack": stack,
        "rules": rules,
        "likely_files": likely,
        "pr_description": pr_description,
        "pr_template_source": (
            templates[0]["path"] if templates else "assets/pr-description-template.md"
        ),
        "claim_comment": _claim_comment(),
        "warnings": warnings + repo_info["warnings"],
    }
    data["markdown"] = render_markdown(data)
    return data


def _claim_comment() -> str:
    text = (ASSETS / "claim-comment-template.md").read_text(encoding="utf-8")
    return Template(text).safe_substitute(
        plan_one_line="<one line: what you will change>", timeframe="<e.g. a week>"
    ).strip()


def render_markdown(data: dict[str, Any], approach: str | None = None) -> str:
    """Render the plan Markdown from collected data (approach can come from an LLM)."""
    issue, stack = data["issue"], data["stack"]
    repo_name = data["repo"].split("/")[1]
    likely = "\n".join(
        f"- `{f['path']}` (matches: {', '.join(f['matched'])})" for f in data["likely_files"]
    ) or "- No obvious matches from the issue text — search the codebase for terms in the issue."
    lint = stack["lint_commands"]
    lint_section = (
        "\nLint before committing:\n```bash\n" + _code_lines(lint, "") + "\n```\n" if lint else ""
    )
    default_approach = (
        "1. Reproduce the problem (or confirm the missing behavior) locally.\n"
        "2. Read the likely files above and find where the change belongs.\n"
        "3. Write or update a test that fails before your change.\n"
        "4. Make the smallest change that makes the test pass.\n"
        "5. Run the full test suite and linters, then review your diff with `git diff`."
    )
    tmpl = Template((ASSETS / "plan-template.md").read_text(encoding="utf-8"))
    md = tmpl.safe_substitute(
        issue_title=issue["title"], repo=data["repo"], repo_name=repo_name,
        issue_number=issue["number"], issue_url=issue["url"],
        labels=", ".join(issue["labels"]) or "none", branch=data["branch"],
        default_branch=data["default_branch"],
        setup_commands=_code_lines(stack["install_commands"],
                                   "no install command detected — see README"),
        test_commands=_code_lines(stack["test_commands"], "no test command detected — see README"),
        lint_section=lint_section, rules=format_rules(data["rules"]), likely_files=likely,
        approach=approach or default_approach, commit_flags="-s " if data["dco"] else "",
        commit_message=data["commit_message"].replace('"', '\\"'),
        pr_description=data["pr_description"],
    )
    md += (
        "\n## Optional: claim the issue\nOnly if the project expects it (see rules above), "
        "post this yourself:\n\n> " + data["claim_comment"].replace("\n", "\n> ") + "\n"
    )
    if data["warnings"]:
        md = "> **Warnings:** " + " ".join(data["warnings"]) + "\n\n" + md
    return md


def main() -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("issue_url", help="https://github.com/OWNER/REPO/issues/N")
    ap.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    args = ap.parse_args()
    data = plan_contribution(args.issue_url)
    if args.json:
        emit_json(data)
    else:
        emit_text(data["markdown"])
    return 0


if __name__ == "__main__":
    run_cli(main)
