#!/usr/bin/env python3
"""What kind of contribution an issue is, what it needs to know, and how well that fits you.

Usage:
    python scripts/find_issues.py OWNER/REPO > issues.json
    python scripts/match_skills.py issues.json --skills "python, react"   # or - for stdin

Adds to each issue:
  "contribution": {"type": docs | typo | tests | translation | code, "label", "low_risk",
                   "reason"}. Non-code work (docs, typos, tests, translations) is a
                   lower-risk first PR: no feature logic to get wrong.
  "techs":        technologies the issue probably needs, from its labels, file names,
                   code blocks and wording (falling back to the repo's main language).
  "skill_match":  with --skills: {"fit": good | partial | stretch | any, "known",
                   "missing"}.
No network access.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

from _github import GitHubError, emit_json, run_cli

# --- contribution types -----------------------------------------------------------------

# (type, label shown, label pattern, title/body pattern). First match wins; "code" otherwise.
CONTRIBUTION_TYPES: tuple[tuple[str, str, str, str], ...] = (
    (
        "translation",
        "Translation",
        r"translat|\bi18n\b|\bl10n\b|locali[sz]ation|internationali[sz]ation",
        r"\btranslat(e|ion|ing)\b|\blocali[sz](e|ation)\b|\bi18n\b|\bl10n\b",
    ),
    (
        "typo",
        "Typo",
        r"typo|spelling|grammar|wording",
        r"\btypos?\b|\bmisspel|\bspelling\b|\bgrammar\b|\bbroken links?\b|\bdead links?\b",
    ),
    (
        "docs",
        "Docs",
        r"\bdocs?\b|documentation|readme|tutorial|guide",
        r"\b(docs?|documentation|readme|docstrings?|tutorial|guide|changelog)\b",
    ),
    (
        "tests",
        "Tests",
        r"\btests?\b|testing|test[\s_-]*coverage|unit[\s_-]*tests?",
        r"\b(add|write|missing|increase|improve)\b.{0,30}\b(tests?|test coverage|unit tests?)\b"
        r"|\btest coverage\b",
    ),
)
NON_CODE = {"docs", "typo", "translation", "tests"}
# Labels that say "this changes behaviour", which outweigh a docs-sounding title.
CODE_LABELS = re.compile(r"\bbug\b|feature|enhancement|regression|crash|performance", re.I)


def contribution_type(issue: dict[str, Any]) -> dict[str, Any]:
    """Docs, typo, tests, translation, or code: labels decide first, then the title."""
    labels = " | ".join(issue.get("labels", []))
    title = issue.get("title", "")
    for kind, label, label_re, _ in CONTRIBUTION_TYPES:
        if re.search(label_re, labels, re.I):
            return _ctype(kind, label, f"labelled {_matching_label(issue, label_re)}")
    if not CODE_LABELS.search(labels):
        for kind, label, _, text_re in CONTRIBUTION_TYPES:
            if re.search(text_re, title, re.I):
                return _ctype(kind, label, "the title describes it")
    return _ctype("code", "Code", "changes the program's behaviour")


def _ctype(kind: str, label: str, reason: str) -> dict[str, Any]:
    return {"type": kind, "label": label, "low_risk": kind in NON_CODE, "reason": reason}


def _matching_label(issue: dict[str, Any], pattern: str) -> str:
    return next(
        (f'"{lbl}"' for lbl in issue.get("labels", []) if re.search(pattern, lbl, re.I)), "that way"
    )


# --- technologies --------------------------------------------------------------------------

# canonical name -> (aliases people type, file extensions, words/labels that mention it)
TECHS: dict[str, tuple[tuple[str, ...], tuple[str, ...], str]] = {
    "Python": (("py", "python3"), (".py", ".pyi", ".ipynb"), r"python|pytest|django|flask|pip\b"),
    "JavaScript": (
        ("js", "node", "nodejs", "node.js", "ecmascript"),
        (".js", ".mjs", ".cjs", ".jsx"),
        r"javascript|node\.?js|\bnpm\b",
    ),
    "TypeScript": (("ts",), (".ts", ".tsx", ".mts"), r"typescript"),
    "React": (("reactjs", "react.js"), (".jsx", ".tsx"), r"\breact\b|\bjsx\b|use(State|Effect)"),
    "Vue": (("vuejs", "vue.js"), (".vue",), r"\bvue\b"),
    "Svelte": ((), (".svelte",), r"\bsvelte\b"),
    "HTML": (("html5",), (".html", ".htm"), r"\bhtml\b"),
    "CSS": (
        ("css3", "scss", "sass", "tailwind", "tailwindcss"),
        (".css", ".scss", ".sass", ".less"),
        r"\bcss\b|\bscss\b|tailwind|styling|stylesheet",
    ),
    "Go": (("golang",), (".go",), r"\bgolang\b|\bgo (module|test|mod)\b"),
    "Rust": ((), (".rs",), r"\brust\b|\bcargo\b"),
    "Java": ((), (".java",), r"\bjava\b(?!script)|\bgradle\b|\bmaven\b"),
    "Kotlin": ((), (".kt", ".kts"), r"\bkotlin\b"),
    "C": ((), (".c", ".h"), r"\bC language\b"),
    "C++": (("cpp", "c plus plus"), (".cpp", ".cc", ".hpp", ".cxx"), r"c\+\+"),
    # Not a bare ".net": that's also every jsfiddle.net / sourceforge.net link.
    "C#": (("csharp", "c sharp", ".net", "dotnet"), (".cs", ".csproj"),
           r"(?<![\w.])c#(?!\w)|\bdotnet\b|(?<![\w/])\.net\b(?! ?/)|\basp\.net\b"),
    "Ruby": (("rb",), (".rb",), r"\bruby\b|\brails\b"),
    "PHP": ((), (".php",), r"\bphp\b|laravel"),
    "Swift": ((), (".swift",), r"\bswift\b"),
    "Dart": (("flutter",), (".dart",), r"\bdart\b|flutter"),
    "Shell": (
        ("bash", "sh", "zsh", "powershell"),
        (".sh", ".bash", ".ps1"),
        r"\bbash\b|shell script",
    ),
    "SQL": (
        ("postgres", "postgresql", "mysql", "sqlite"),
        (".sql",),
        r"\bsql\b|postgres|mysql|sqlite|database migration",
    ),
    "Docker": (
        ("dockerfile", "containers"),
        ("dockerfile",),
        r"\bdocker\b|dockerfile|container image",
    ),
    "CI/YAML": (
        ("github actions", "ci", "yaml", "yml"),
        (".yml", ".yaml"),
        r"github actions|\bworkflow\b|\bci\b|\byaml\b",
    ),
    "Markdown/Writing": (
        ("markdown", "md", "writing", "docs", "documentation", "technical writing"),
        (".md", ".mdx", ".rst", ".txt"),
        r"markdown|\bdocs?\b|documentation|readme",
    ),
}
# GitHub's repo language names that differ from ours.
GITHUB_LANGUAGES = {
    "Jupyter Notebook": "Python",
    "Vue": "Vue",
    "SCSS": "CSS",
    "Dockerfile": "Docker",
    "Svelte": "Svelte",
    "Astro": "JavaScript",
}
FENCE_RE = re.compile(r"```\s*([A-Za-z+#]+)")
FENCE_LANG = {
    "py": "Python",
    "python": "Python",
    "js": "JavaScript",
    "javascript": "JavaScript",
    "jsx": "React",
    "ts": "TypeScript",
    "tsx": "React",
    "typescript": "TypeScript",
    "go": "Go",
    "rust": "Rust",
    "rs": "Rust",
    "java": "Java",
    "kotlin": "Kotlin",
    "c": "C",
    "cpp": "C++",
    "c++": "C++",
    "cs": "C#",
    "csharp": "C#",
    "ruby": "Ruby",
    "php": "PHP",
    "swift": "Swift",
    "dart": "Dart",
    "bash": "Shell",
    "sh": "Shell",
    "shell": "Shell",
    "sql": "SQL",
    "html": "HTML",
    "css": "CSS",
    "scss": "CSS",
    "yaml": "CI/YAML",
    "yml": "CI/YAML",
    "dockerfile": "Docker",
    "vue": "Vue",
    "svelte": "Svelte",
}
PATH_RE = re.compile(r"[\w./-]+(\.[A-Za-z]{1,6})\b|\bDockerfile\b")


def skill_aliases() -> dict[str, str]:
    """Every name a user might type (lower-case) -> the canonical technology."""
    out = {}
    for name, (aliases, _, _) in TECHS.items():
        out[name.lower()] = name
        for alias in aliases:
            out[alias.lower()] = name
    return out


ALIASES = skill_aliases()


def normalize_skills(skills: list[str] | str) -> list[str]:
    """User input ("js, React.js, golang") -> canonical names (JavaScript, React, Go).
    Unknown names are kept as typed, so the user still sees them."""
    if isinstance(skills, str):
        skills = re.split(r"[,;\n]+", skills)
    out: list[str] = []
    for raw in skills:
        s = raw.strip()
        if not s:
            continue
        name = ALIASES.get(s.lower()) or ALIASES.get(re.sub(r"[\s.]+(js)?$", "", s.lower())) or s
        if name not in out:
            out.append(name)
    return out


def detect_techs(issue: dict[str, Any], repo_languages: list[str] | None = None) -> list[str]:
    """Technologies an issue probably needs, most evident first."""
    contribution = issue.get("contribution") or contribution_type(issue)
    if contribution["type"] in ("docs", "typo", "translation"):
        return ["Markdown/Writing"]
    text = f"{issue.get('title', '')}\n{issue.get('body') or ''}"
    labels = " | ".join(issue.get("labels", []))
    scores: dict[str, float] = {}

    def add(name: str, weight: float) -> None:
        scores[name] = scores.get(name, 0) + weight

    code_techs = {n: t for n, t in TECHS.items() if n != "Markdown/Writing"}  # code issues
    for m in PATH_RE.finditer(text):
        ext = (m.group(1) or "dockerfile").lower()
        for name, (_, exts, _) in code_techs.items():
            if ext in exts:
                add(name, 3)
    for m in FENCE_RE.finditer(text):
        if name := FENCE_LANG.get(m.group(1).lower()):
            add(name, 2)
    for name, (_, _, words) in code_techs.items():
        if re.search(words, labels, re.I):
            add(name, 4)
        if re.search(words, text, re.I):
            add(name, 1)
    if not scores and repo_languages:  # nothing in the issue itself: the repo's main language
        add(GITHUB_LANGUAGES.get(repo_languages[0], repo_languages[0]), 1)
    if not scores:
        return []
    ranked = sorted(scores, key=lambda n: -scores[n])
    return [n for n in ranked if scores[n] >= min(2, max(scores.values()))][:4]


def skill_fit(techs: list[str], skills: list[str]) -> dict[str, Any]:
    """How well what you know covers what the issue needs."""
    known_set = {s.lower() for s in normalize_skills(skills)}
    if not techs:
        return {"fit": "any", "known": [], "missing": []}
    known = [t for t in techs if t.lower() in known_set]
    missing = [t for t in techs if t.lower() not in known_set]
    if techs == ["Markdown/Writing"]:  # docs, typos, translations: anyone can start
        return {"fit": "good", "known": known or techs, "missing": []}
    fit = "good" if not missing else "partial" if known else "stretch"
    return {"fit": fit, "known": known, "missing": missing}


def annotate(
    issues: list[dict[str, Any]],
    repo_languages: list[str] | None = None,
    skills: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Add contribution, techs and (with skills) skill_match to each issue."""
    out = []
    for issue in issues:
        item = {**issue, "contribution": contribution_type(issue)}
        item["techs"] = detect_techs(item, repo_languages)
        if skills:
            item["skill_match"] = skill_fit(item["techs"], skills)
        out.append(item)
    return out


def main() -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("source", nargs="?", default="-", help="find_issues.py JSON, or - for stdin")
    ap.add_argument("--skills", default="", help='what you know, e.g. "python, react, css"')
    args = ap.parse_args()
    try:
        raw = sys.stdin.read() if args.source == "-" else open(args.source, encoding="utf-8").read()
        data = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise GitHubError(f"Cannot read issues JSON from {args.source}: {exc}") from exc
    issues = data["issues"] if isinstance(data, dict) else data
    result = annotate(
        issues,
        data.get("languages") if isinstance(data, dict) else None,
        normalize_skills(args.skills) if args.skills else None,
    )
    emit_json({**data, "issues": result} if isinstance(data, dict) else result)
    return 0


if __name__ == "__main__":
    run_cli(main)
