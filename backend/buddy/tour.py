"""Codebase tour: how a repo is organized and where things happen, for a first-timer.

Facts come from the chat's code index (rag.py): the top-level folders (file counts,
languages, their own README's first paragraph, sample files), the entry points with
their first lines, and where tests, docs and CI live. The model turns them into a
walkthrough with [path:lines] citations, which are then checked like chat answers.
Without a model, the facts alone make a plain tour.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from buddy import citations, llm, rag
from buddy.providers import Provider, ProviderError
from buddy.skill import repo_docs

HEAD_LINES = 30      # lines of each entry point shown to the model
MAX_AREAS = 12
SAMPLE_FILES = 5
ENTRY_NAMES = re.compile(
    r"(^|/)(__main__\.py|main\.(py|go|rs|ts|js|tsx|jsx|java|kt|c|cpp)|app\.(py|ts|js|tsx|jsx)|"
    r"manage\.py|cli\.(py|ts|js)|server\.(py|ts|js)|index\.(ts|js|tsx|jsx)|lib\.rs|mod\.rs)$")
TEST_DIR = re.compile(r"(^|/)(tests?|__tests__|spec|e2e|cypress)(/|$)", re.I)
DOC_DIR = re.compile(r"(^|/)(docs?|documentation|site|website)(/|$)", re.I)
EXT_LANGUAGE = {"py": "Python", "js": "JavaScript", "jsx": "React", "ts": "TypeScript",
                "tsx": "React", "go": "Go", "rs": "Rust", "java": "Java", "kt": "Kotlin",
                "rb": "Ruby", "php": "PHP", "c": "C", "h": "C", "cpp": "C++", "cs": "C#",
                "swift": "Swift", "vue": "Vue", "svelte": "Svelte", "css": "CSS",
                "scss": "CSS", "html": "HTML", "md": "Markdown", "rst": "Docs",
                "yml": "YAML", "yaml": "YAML", "json": "JSON", "toml": "TOML", "sh": "Shell"}


def _first_paragraph(lines: list[str]) -> str:
    """A README's first real paragraph (no headings, badges or HTML)."""
    para: list[str] = []
    for line in lines[:60]:
        text = line.strip()
        if not text:
            if para:
                break
            continue
        if text.startswith(("#", "<", "[![", "![", "---", "===")):
            continue
        para.append(text)
    return " ".join(para)[:300]


def _docstring(lines: list[str]) -> str:
    """A Python module docstring's first line, or a JS/Go file's leading comment."""
    head = "\n".join(lines[:15])
    m = re.match(r'\s*(?:#!.*\n)?\s*[rRuU]?("""|\'\'\')\s*(.+?)(?:\n|\1)', head)
    if m:
        return m.group(2).strip()[:160]
    m = re.match(r"\s*(?://|/\*\*?|\*)\s*(.+)", head)
    return m.group(1).strip(" */")[:160] if m else ""


BORING = re.compile(r"(^|/)(__init__\.py|conftest\.py|setup\.py|\.gitkeep|py\.typed)$|"
                    r"\.(lock|min\.js|map|snap)$")


def _importance(path: str, index: rag.RepoIndex) -> float:
    """How useful a file is to show first: entry points, documented and substantial files,
    near the folder's top; not empty package markers or fixtures."""
    lines = index.lines.get(path, [])
    score = min(len(lines), 400) / 100 + (3 if _docstring(lines) else 0) - path.count("/")
    if ENTRY_NAMES.search(path):
        score += 5
    if BORING.search(path) or len(lines) < 5:
        score -= 6
    return score


def areas(paths: list[str], index: rag.RepoIndex) -> list[dict[str, Any]]:
    """Top-level folders (and the root's own files), biggest first."""
    groups: dict[str, list[str]] = {}
    for p in paths:
        groups.setdefault(p.split("/", 1)[0] if "/" in p else "(root)", []).append(p)
    out = []
    for folder, files in groups.items():
        langs = Counter(EXT_LANGUAGE.get(f.rsplit(".", 1)[-1].lower(), "") for f in files)
        langs.pop("", None)
        readme = next((f for f in files if re.fullmatch(
            rf"{re.escape(folder)}/readme(\.\w+)?", f, re.I)), None) if folder != "(root)" else None
        sample = [{"path": f, "about": _docstring(index.lines.get(f, []))}
                  for f in sorted(files, key=lambda f: -_importance(f, index))[:SAMPLE_FILES]]
        kind = ("tests" if TEST_DIR.search(folder + "/") else "docs" if DOC_DIR.search(folder + "/")
                else "ci" if folder in (".github", ".circleci", ".gitlab") else "code")
        out.append({"folder": folder, "files": len(files), "kind": kind,
                    "languages": [n for n, _ in langs.most_common(3)],
                    "readme": readme,
                    "description": _first_paragraph(index.lines[readme]) if readme else "",
                    "sample": sample})
    out.sort(key=lambda a: (a["folder"] == "(root)", -a["files"]))
    return out[:MAX_AREAS]


def entry_points(index: rag.RepoIndex, info: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Where the program starts: conventional file names plus package metadata."""
    found: list[str] = []
    pkg_text = "\n".join(index.lines.get("package.json", []))
    try:
        pkg = json.loads(pkg_text) if pkg_text else {}
    except ValueError:
        pkg = {}
    bins = pkg.get("bin") if isinstance(pkg.get("bin"), dict) else {"": pkg.get("bin")}
    for value in [pkg.get("main"), pkg.get("module"), *bins.values()]:
        if isinstance(value, str) and (path := index.resolve(value)):
            found.append(path)
    py = "\n".join(index.lines.get("pyproject.toml", []))
    for module in re.findall(r'^\s*[\w-]+\s*=\s*"([\w.]+):\w+"', py, re.M):
        base = module.replace(".", "/")
        for candidate in (f"{base}.py", f"src/{base}.py", f"{base}/__init__.py"):
            if path := index.resolve(candidate):
                found.append(path)
                break
    by_name = sorted((p for p in index.lines if ENTRY_NAMES.search(p)
                      and not TEST_DIR.search(p) and p.count("/") <= 3),
                     key=lambda p: (p.count("/"), len(p)))
    found += by_name
    out = []
    for path in list(dict.fromkeys(found))[:6]:
        lines = index.lines[path]
        out.append({"path": path, "start": 1, "end": min(len(lines), HEAD_LINES),
                    "about": _docstring(lines), "head": "\n".join(lines[:HEAD_LINES])})
    return out


def tour_facts(index: rag.RepoIndex, info: dict[str, Any] | None) -> dict[str, Any]:
    """Everything the tour is built from."""
    paths = sorted(index.lines)
    tests = sorted({p.split("/", 1)[0] for p in paths if TEST_DIR.search(p)})[:4]
    docs = sorted({p.split("/", 1)[0] for p in paths if DOC_DIR.search(p.split("/", 1)[0] + "/")})
    stack = (info or {}).get("stack") or {}
    return {
        "repo": index.repo, "branch": index.ref, "files": len(paths),
        "description": (info or {}).get("description"),
        "languages": [lang["name"] for lang in stack.get("languages", [])[:4]],
        "areas": areas(paths, index), "entry_points": entry_points(index, info),
        "tests_in": tests, "docs_in": docs,
        "ci": sorted(p for p in paths if p.startswith(".github/workflows/"))[:6],
        "test_commands": [c["command"] for c in stack.get("test_commands", [])[:2]],
    }


def plain_tour(facts: dict[str, Any]) -> str:
    """A tour without a model: the facts as Markdown."""
    lines = [f"## How {facts['repo']} is organized", ""]
    if facts["description"]:
        lines += [facts["description"], ""]
    for a in facts["areas"]:
        what = a["description"] or ", ".join(a["languages"]) or a["kind"]
        lines.append(f"- **{a['folder']}/** ({a['files']} files): {what}")
    if facts["entry_points"]:
        lines += ["", "## Where it starts", ""]
        lines += [f"- `{e['path']}`" + (f": {e['about']}" if e["about"] else "")
                  for e in facts["entry_points"]]
    lines += ["", "## Suggested reading order", "", "1. `README` and `CONTRIBUTING`"]
    if facts["entry_points"]:
        lines.append(f"2. `{facts['entry_points'][0]['path']}`, where the program starts")
    if facts["tests_in"]:
        lines.append(f"3. A test in `{facts['tests_in'][0]}/` close to what you'll change")
    return "\n".join(lines)


def model_facts(facts: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """(facts without code, the code as cited snippets) for the prompt."""
    slim = {**facts, "entry_points": [{k: e[k] for k in ("path", "about")}
                                      for e in facts["entry_points"]]}
    snippets = "\n\n".join(f"[{e['path']}:{e['start']}-{e['end']}]\n```\n{e['head']}\n```"
                           for e in facts["entry_points"])
    return slim, snippets or "(no entry points found)"


def run(repo: str, provider: Provider | None) -> dict[str, Any]:
    """The tour for a repo: facts, Markdown and checked citations."""
    info = repo_docs.fetch_repo_docs(repo, health=False)
    index = rag.get_index(*info["repo"].split("/"), info["default_branch"])
    facts = tour_facts(index, info)
    result: dict[str, Any] = {"repo": info["repo"], "branch": info["default_branch"],
                              "facts": facts, "markdown": plain_tour(facts), "citations": [],
                              "ai": False, "ai_error": None, "model": None}
    if provider:
        slim, snippets = model_facts(facts)
        try:
            markdown = llm.ask(provider, "tour", repo=info["repo"], snippets=snippets,
                               facts=json.dumps(slim, ensure_ascii=False))
        except ProviderError as exc:
            result["ai_error"] = f"{exc} Showing the plain tour instead."
            return result
        known = citations.Known.from_stack(info.get("stack") or {}, navigational=True,
                                           skip={info["repo"]})
        result.update(markdown=markdown, ai=True, model=provider.model,
                      citations=citations.verify(markdown, index, known))
    return result
