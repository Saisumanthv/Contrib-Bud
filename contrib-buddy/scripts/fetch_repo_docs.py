#!/usr/bin/env python3
"""Fetch a repo's contributor docs and detect its stack.

Usage:
    python scripts/fetch_repo_docs.py OWNER/REPO [--max-chars N]

Prints JSON with: repo metadata, docs (README, CONTRIBUTING, CODE_OF_CONDUCT,
PR template, issue templates), detected stack (languages, package manager,
install/test/lint commands, each with the file it came from), and
contribution rules (DCO, CLA, issue-link requirement, commit convention...).
Uses 3 GitHub API calls; file contents come from raw.githubusercontent.com.
"""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from typing import Any

from _github import GitHubError, api_get, emit_json, parse_repo, raw_get, run_cli, truncate

# --- doc discovery ----------------------------------------------------------

DOC_DIRS = ("", ".github/", "docs/")


def find_doc(paths: set[str], stem: str) -> str | None:
    """Find a doc like CONTRIBUTING in root, .github/ or docs/ (case-insensitive)."""
    lower = {p.lower(): p for p in paths}
    for d in DOC_DIRS:
        for ext in (".md", ".rst", ".txt", ".adoc", ""):
            hit = lower.get(f"{d}{stem}{ext}".lower())
            if hit:
                return hit
    return None


def find_pr_templates(paths: set[str]) -> list[str]:
    """Return PR template paths (single-file and multi-template folder forms)."""
    found = []
    for p in sorted(paths):
        lp = p.lower()
        name = lp.rsplit("/", 1)[-1]
        if name in ("pull_request_template.md", "pull_request_template.txt") and lp.count("/") <= 1:
            found.append(p)
        elif lp.startswith(".github/pull_request_template/") and lp.endswith(".md"):
            found.append(p)
    return found


def find_issue_templates(paths: set[str]) -> list[str]:
    """Return issue template paths under .github/ISSUE_TEMPLATE/."""
    return sorted(
        p for p in paths
        if p.lower().startswith(".github/issue_template/")
        and p.lower().endswith((".md", ".yml", ".yaml"))
        and not p.lower().endswith("config.yml")
    )


# --- stack detection (pure) -------------------------------------------------

STACK_FILES = (
    "package.json", "pyproject.toml", "setup.cfg", "setup.py", "tox.ini", "noxfile.py",
    "Makefile", "justfile", "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
    "build.gradle.kts", "Gemfile", "composer.json", ".pre-commit-config.yaml",
)

# ecosystem -> ordered (marker file, package manager); first match per ecosystem wins
ECOSYSTEM_MARKERS: dict[str, tuple[tuple[str, str], ...]] = {
    "javascript": (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("bun.lockb", "bun"),
                   ("bun.lock", "bun"), ("package-lock.json", "npm"), ("package.json", "npm")),
    "python": (("poetry.lock", "poetry"), ("uv.lock", "uv"), ("Pipfile", "pipenv"),
               ("pyproject.toml", "pip"), ("setup.py", "pip")),
    "rust": (("Cargo.toml", "cargo"),),
    "go": (("go.mod", "go"),),
    "java": (("pom.xml", "maven"), ("build.gradle", "gradle"), ("build.gradle.kts", "gradle")),
    "ruby": (("Gemfile", "bundler"),),
    "php": (("composer.json", "composer"),),
}

INSTALL = {
    "npm": "npm install", "pnpm": "pnpm install", "yarn": "yarn install", "bun": "bun install",
    "poetry": "poetry install", "uv": "uv sync", "pipenv": "pipenv install --dev",
    "pip": "pip install -e .", "cargo": "cargo build", "go": "go mod download",
    "maven": "mvn install -DskipTests", "gradle": "./gradlew build -x test",
    "bundler": "bundle install", "composer": "composer install",
}

JS_RUN = {"npm": "npm run", "pnpm": "pnpm", "yarn": "yarn", "bun": "bun run"}
PY_RUNNERS = {"uv": "uv run", "poetry": "poetry run", "pipenv": "pipenv run"}
PY_TOOLS = {"pytest", "tox", "nox", "ruff", "flake8", "black", "mypy", "pre-commit"}


def _cmd(command: str, source: str) -> dict[str, str]:
    return {"command": command, "source": source}


def _add(items: list[dict[str, str]], command: str, source: str) -> None:
    if all(i["command"] != command for i in items):
        items.append(_cmd(command, source))


def detect_package_managers(paths: set[str], files: dict[str, str]) -> dict[str, tuple[str, str]]:
    """Return {ecosystem: (package_manager, evidence_file)} from lockfiles and manifests."""
    found: dict[str, tuple[str, str]] = {}
    for eco, markers in ECOSYSTEM_MARKERS.items():
        for marker, pm in markers:
            if marker in paths:
                found[eco] = (pm, marker)
                break
    is_poetry = "[tool.poetry]" in files.get("pyproject.toml", "")
    if found.get("python", ("",))[0] == "pip" and is_poetry:
        found["python"] = ("poetry", "pyproject.toml")
    if "python" not in found:
        reqs = sorted(p for p in paths if re.fullmatch(r"requirements[\w-]*\.txt", p))
        if reqs:
            found["python"] = ("pip", reqs[0])
    return found


def _makefile_targets(text: str) -> set[str]:
    return set(re.findall(r"^([A-Za-z][\w-]*)\s*:(?!=)", text, re.MULTILINE))


def _workflow_runs(text: str) -> list[str]:
    """Extract single-line `run:` commands from a GitHub Actions workflow."""
    runs = re.findall(r"^\s*-?\s*run:\s*([^|>\n][^\n]*)$", text, re.MULTILINE)
    return [r.strip().strip("'\"") for r in runs]


def detect_stack(
    paths: set[str], files: dict[str, str], languages: dict[str, int] | None = None
) -> dict[str, Any]:
    """Detect languages, package manager and install/test/lint commands.

    Args:
        paths: All file paths in the repo (relative, '/'-separated).
        files: Contents of the stack-relevant files that exist (path -> text).
        languages: GitHub's language byte counts, if available.

    Returns:
        Dict with languages, package_managers, install_commands, test_commands,
        lint_commands and ci_commands; every command carries its source file.
    """
    managers = detect_package_managers(paths, files)
    install: list[dict[str, str]] = []
    tests: list[dict[str, str]] = []
    lint: list[dict[str, str]] = []

    for pm, src in managers.values():
        if pm == "pip" and src.endswith(".txt"):
            _add(install, f"pip install -r {src}", src)
        else:
            _add(install, INSTALL[pm], src)

    # JavaScript / TypeScript
    if "package.json" in files:
        try:
            scripts = json.loads(files["package.json"]).get("scripts", {}) or {}
        except (ValueError, AttributeError):
            scripts = {}
        js_pm = managers.get("javascript", ("npm", ""))[0]
        if "test" in scripts and "no test specified" not in str(scripts["test"]):
            _add(tests, "npm test" if js_pm == "npm" else f"{js_pm} test", "package.json")
        for name in scripts:
            if name in ("lint", "typecheck", "check-types", "format:check", "prettier:check"):
                _add(lint, f"{JS_RUN[js_pm]} {name}", "package.json")

    # Python
    pyproject = files.get("pyproject.toml", "")
    py_cfg: dict[str, Any] = {}
    if pyproject:
        try:
            py_cfg = tomllib.loads(pyproject)
        except tomllib.TOMLDecodeError:
            py_cfg = {}
    tool = py_cfg.get("tool", {}) if isinstance(py_cfg, dict) else {}
    if "tox.ini" in paths:
        _add(tests, "tox", "tox.ini")
    elif "tox" in tool:
        _add(tests, "tox", "pyproject.toml")
    if "noxfile.py" in paths:
        _add(tests, "nox", "noxfile.py")
    if "pytest" in tool or "pytest" in pyproject or "[tool:pytest]" in files.get("setup.cfg", "") \
            or "pytest.ini" in paths or "conftest.py" in paths:
        src = "pytest.ini" if "pytest.ini" in paths else "pyproject.toml"
        _add(tests, "pytest", src)
    if "ruff" in tool or "ruff.toml" in paths or ".ruff.toml" in paths:
        _add(lint, "ruff check .", "ruff.toml" if "ruff.toml" in paths else "pyproject.toml")
    if ".flake8" in paths or "[flake8]" in files.get("setup.cfg", "") \
            or "[flake8]" in files.get("tox.ini", ""):
        _add(lint, "flake8", ".flake8" if ".flake8" in paths else "setup.cfg")
    if "black" in tool:
        _add(lint, "black --check .", "pyproject.toml")
    if "mypy" in tool or "mypy.ini" in paths:
        _add(lint, "mypy .", "mypy.ini" if "mypy.ini" in paths else "pyproject.toml")

    # Other ecosystems
    if "Cargo.toml" in paths:
        _add(tests, "cargo test", "Cargo.toml")
        _add(lint, "cargo clippy -- -D warnings", "Cargo.toml")
    if "go.mod" in paths:
        _add(tests, "go test ./...", "go.mod")
        if any(p in paths for p in (".golangci.yml", ".golangci.yaml")):
            _add(lint, "golangci-lint run", ".golangci.yml")
    if "pom.xml" in paths:
        _add(tests, "mvn test", "pom.xml")
    if "build.gradle" in paths or "build.gradle.kts" in paths:
        runner = "./gradlew" if "gradlew" in paths else "gradle"
        _add(tests, f"{runner} test", "build.gradle")
    if "Gemfile" in paths and any(p.startswith("spec/") for p in paths):
        _add(tests, "bundle exec rspec", "Gemfile")

    # JS linters by config file
    if any(re.match(r"^(\.eslintrc(\.\w+)?|eslint\.config\.\w+)$", p) for p in paths) \
            and not any("lint" in c["command"] for c in lint):
        _add(lint, "npx eslint .", "eslint config")

    # Makefile targets
    targets = _makefile_targets(files.get("Makefile", ""))
    for t in ("test", "tests", "check"):
        if t in targets:
            _add(tests, f"make {t}", "Makefile")
    for t in ("lint", "fmt-check", "format-check"):
        if t in targets:
            _add(lint, f"make {t}", "Makefile")
    if ".pre-commit-config.yaml" in paths:
        _add(lint, "pre-commit run --all-files", ".pre-commit-config.yaml")

    # CI workflows: evidence of what maintainers actually run
    ci: list[dict[str, str]] = []
    for path, text in sorted(files.items()):
        if path.startswith(".github/workflows/"):
            for run in _workflow_runs(text):
                if re.search(r"\b(test|lint|check|pytest|jest|vitest|ruff|eslint|tox|clippy)\b",
                             run):
                    _add(ci, run, path)
    ci = ci[:12]

    # Python tools run inside the project's managed environment
    py_runner = PY_RUNNERS.get(managers.get("python", ("", ""))[0])
    if py_runner:
        for items in (tests, lint):
            for item in items:
                if item["command"].split()[0] in PY_TOOLS:
                    item["command"] = f"{py_runner} {item['command']}"

    langs = sorted((languages or {}).items(), key=lambda kv: -kv[1])
    total = sum(v for _, v in langs) or 1
    return {
        "languages": [{"name": k, "percent": round(100 * v / total, 1)} for k, v in langs[:6]],
        "package_managers": {
            eco: {"name": pm, "source": src} for eco, (pm, src) in managers.items()
        },
        "install_commands": install,
        "test_commands": tests,
        "lint_commands": lint,
        "ci_commands": ci,
    }


# --- contribution rules (pure) ----------------------------------------------

RULE_PATTERNS: dict[str, str] = {
    "dco": r"signed-off-by|developer certificate of origin|\bDCO\b|git commit -s\b|--signoff",
    "cla": r"\bCLA\b|[Cc]ontributor [Ll]icense [Aa]greement",
    "issue_link_required": (
        r"(link|reference|refer to|mention)\w*\s+(to\s+)?(an?|the|your|related)\s+issue"
        r"|(open|create|file)\s+an?\s+issue\s+(first|before)"
        r"|discuss\w*\b.{0,40}\bbefore\b.{0,40}\b(pull request|PR)"
        r"|fixes\s+#|closes\s+#"
    ),
    "conventional_commits": r"conventional\s*commits|conventionalcommits\.org|commitlint",
    "changelog_required": r"\bchangelog\b|\bchangeset",
    "tests_required": r"(add|include|write|update)\w*\s+(\w+\s+)?tests?\b",
    "ai_policy": r"\bAI\b|\bLLMs?\b|[Cc]opilot|ChatGPT|[Mm]achine[- ]generated|[Gg]enerative AI",
    "no_assignment_needed": (
        r"(do not|don't|no need to)\s+(need\s+to\s+)?(ask|wait)\s+(to\s+be\s+|for\s+)?assign"
    ),
    "assignment_required": r"(ask|request)\w*\s+to\s+be\s+assigned|assigned\s+(to you\s+)?before",
}


CASE_SENSITIVE_RULES = {"cla", "ai_policy"}  # acronyms like "CLA"/"AI" must not match words


def _evidence(text: str, pattern: str, ignore_case: bool = True) -> str | None:
    """Return the first line of `text` matching `pattern`."""
    flags = re.IGNORECASE if ignore_case else 0
    for line in text.splitlines():
        if re.search(pattern, line, flags):
            return line.strip()[:240]
    return None


def extract_rules(docs: dict[str, str], paths: set[str] | None = None) -> dict[str, Any]:
    """Detect contribution rules from doc texts and config files.

    Args:
        docs: Mapping of source file name -> text (CONTRIBUTING, PR template, ...).
        paths: Repo file paths, used for config-based evidence (commitlint, DCO app).

    Returns:
        Mapping rule -> {"value": bool, "source": file, "evidence": line} for
        detected rules, plus "branch_prefixes" if the docs show a convention.
    """
    paths = paths or set()
    rules: dict[str, Any] = {}
    for rule, pattern in RULE_PATTERNS.items():
        for source, text in docs.items():
            line = _evidence(text or "", pattern, rule not in CASE_SENSITIVE_RULES)
            if line:
                rules[rule] = {"value": True, "source": source, "evidence": line}
                break
    config_evidence = {
        "conventional_commits": ("commitlint.config.js", "commitlint.config.ts",
                                 "commitlint.config.cjs", "commitlint.config.mjs",
                                 ".commitlintrc", ".commitlintrc.json", ".commitlintrc.yml"),
        "dco": (".github/dco.yml",),
        "changelog_required": (".changeset/config.json",),
    }
    for rule, files in config_evidence.items():
        hit = next((f for f in files if f in paths), None)
        if hit and rule not in rules:
            rules[rule] = {"value": True, "source": hit, "evidence": f"{hit} exists"}

    prefixes: dict[str, str] = {}
    for source, text in docs.items():
        for m in re.finditer(
            r"\b(feature|feat|fix|bugfix|hotfix|docs|chore|refactor|test)/[a-z0-9{<\[]",
            text or "",
        ):
            prefixes.setdefault(m.group(1), source)
    if prefixes:
        rules["branch_prefixes"] = {
            "value": sorted(prefixes), "source": next(iter(prefixes.values())),
            "evidence": "branch names shown in docs",
        }
    return rules


# --- orchestration ----------------------------------------------------------

def fetch_tree(owner: str, repo: str, branch: str) -> tuple[set[str], bool]:
    """Return (set of file paths, truncated flag) for the default branch."""
    data = api_get(f"/repos/{owner}/{repo}/git/trees/{branch}", {"recursive": "1"})
    paths = {e["path"] for e in data.get("tree", []) if e.get("type") == "blob"}
    return paths, bool(data.get("truncated"))


def fetch_repo_docs(repo_arg: str, max_chars: int = 12000) -> dict[str, Any]:
    """Collect docs, stack and rules for a repo. See module docstring."""
    owner, repo = parse_repo(repo_arg)
    meta = api_get(
        f"/repos/{owner}/{repo}",
        not_found=f"Repository {owner}/{repo} was not found (or is private). Check the spelling.",
    )
    owner, repo = meta["owner"]["login"], meta["name"]  # canonical casing / renames
    branch = meta["default_branch"]
    paths, tree_truncated = fetch_tree(owner, repo, branch)
    languages = api_get(f"/repos/{owner}/{repo}/languages")

    def get(path: str | None, limit: int) -> dict[str, Any] | None:
        if not path:
            return None
        text, cut = truncate(raw_get(owner, repo, branch, path), limit)
        return {"path": path, "content": text, "truncated": cut}

    readme_path = next((p for p in sorted(paths) if re.fullmatch(r"(?i)readme(\.\w+)?", p)), None)
    pr_paths = find_pr_templates(paths)
    issue_paths = find_issue_templates(paths)
    docs = {
        "readme": get(readme_path, max_chars // 2),
        "contributing": get(find_doc(paths, "CONTRIBUTING"), max_chars),
        "code_of_conduct": get(find_doc(paths, "CODE_OF_CONDUCT"), 1500),
        "pr_templates": [d for p in pr_paths[:3] if (d := get(p, 4000))],
        "issue_templates": [p for p in issue_paths],
    }

    stack_files: dict[str, str] = {}
    wanted = [p for p in STACK_FILES if p in paths]
    wanted += sorted(p for p in paths if re.fullmatch(r"\.github/workflows/[^/]+\.ya?ml", p))[:6]
    for p in wanted:
        text = raw_get(owner, repo, branch, p)
        if text is not None:
            stack_files[p] = text[:60000]
    stack = detect_stack(paths, stack_files, languages)

    rule_docs = {}
    if docs["contributing"]:
        rule_docs[docs["contributing"]["path"]] = docs["contributing"]["content"]
    for t in docs["pr_templates"]:
        rule_docs[t["path"]] = t["content"]
    if docs["readme"]:
        rule_docs[docs["readme"]["path"]] = docs["readme"]["content"]
    rules = extract_rules(rule_docs, paths)

    return {
        "repo": f"{owner}/{repo}",
        "url": meta["html_url"],
        "description": meta.get("description"),
        "default_branch": branch,
        "topics": meta.get("topics", []),
        "license": (meta.get("license") or {}).get("spdx_id"),
        "stars": meta.get("stargazers_count"),
        "open_issues": meta.get("open_issues_count"),
        "archived": meta.get("archived", False),
        "fork": meta.get("fork", False),
        "file_count": len(paths),
        "tree_truncated": tree_truncated,
        "docs": docs,
        "stack": stack,
        "rules": rules,
        "warnings": _warnings(meta, docs),
    }


def _warnings(meta: dict[str, Any], docs: dict[str, Any]) -> list[str]:
    out = []
    if meta.get("archived"):
        out.append("Repository is archived (read-only): contributions are not accepted.")
    if not docs["contributing"]:
        out.append("No CONTRIBUTING file found: follow README instructions and ask maintainers.")
    if not meta.get("license"):
        out.append("No license detected: check with maintainers before contributing.")
    return out


def main() -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("repo", help="owner/repo or GitHub URL")
    ap.add_argument("--max-chars", type=int, default=12000,
                    help="max characters of CONTRIBUTING to include (README gets half)")
    args = ap.parse_args()
    emit_json(fetch_repo_docs(args.repo, args.max_chars))
    return 0


if __name__ == "__main__":
    run_cli(main)


__all__ = ["GitHubError", "detect_stack", "extract_rules", "fetch_repo_docs", "fetch_tree"]
