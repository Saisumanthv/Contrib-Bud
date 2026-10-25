#!/usr/bin/env python3
"""Check your machine has what a repo needs before setup fails in confusing ways.

Usage (inside your local clone):
    python scripts/doctor.py [--path .] [--markdown]

Reads the versions the repo asks for (package.json engines and packageManager,
.nvmrc, .node-version, .tool-versions, pyproject requires-python, .python-version,
runtime.txt, go.mod, rust-toolchain, Cargo.toml rust-version, .ruby-version, Gemfile)
and the tools its install/test/lint commands use, then runs each tool's
--version locally and compares. Prints JSON (or Markdown) and exits 1 if something
required is missing or too old. Changes nothing on your machine.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

from _github import emit_json, emit_text, run_cli
from fetch_repo_docs import STACK_FILES, detect_stack

VERSION_FILES = (".nvmrc", ".node-version", ".tool-versions", ".python-version", "runtime.txt",
                 "rust-toolchain", "rust-toolchain.toml", ".ruby-version", ".java-version")
VERSION_RE = re.compile(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?")
ICON = {"pass": "✅", "warn": "⚠️", "fail": "❌", "missing": "❌", "unknown": "❔"}

# tool -> (commands to try for its version, how to get it)
TOOLS: dict[str, tuple[tuple[tuple[str, ...], ...], str]] = {
    "node": ((("node", "--version"),), "Install it from https://nodejs.org, or with nvm: "
             "`nvm install <version>` then `nvm use`."),
    "npm": ((("npm", "--version"),), "npm comes with Node.js: install or update Node."),
    "pnpm": ((("pnpm", "--version"),), "Run `corepack enable` (ships with Node 16.10+), or "
             "`npm install -g pnpm`."),
    "yarn": ((("yarn", "--version"),), "Run `corepack enable` (ships with Node 16.10+), or "
             "`npm install -g yarn`."),
    "bun": ((("bun", "--version"),), "See https://bun.sh for the installer."),
    "python": ((("python", "--version"), ("python3", "--version"), ("py", "-3", "--version")),
               "Install it from https://www.python.org/downloads/, or with uv: "
               "`uv python install <version>`."),
    "uv": ((("uv", "--version"),), "`pip install uv`, or see https://docs.astral.sh/uv/."),
    "poetry": ((("poetry", "--version"),), "`pipx install poetry` (see https://python-poetry.org)."),
    "pipenv": ((("pipenv", "--version"),), "`pip install --user pipenv`."),
    "pip": ((("pip", "--version"), ("pip3", "--version")), "pip comes with Python."),
    "pre-commit": ((("pre-commit", "--version"),), "`pip install pre-commit`."),
    "tox": ((("tox", "--version"),), "`pip install tox`."),
    "nox": ((("nox", "--version"),), "`pip install nox`."),
    "go": ((("go", "version"),), "Install it from https://go.dev/dl/."),
    "rustc": ((("rustc", "--version"),), "Install with rustup: https://rustup.rs."),
    "cargo": ((("cargo", "--version"),), "Install with rustup: https://rustup.rs."),
    "ruby": ((("ruby", "--version"),), "Install with rbenv, asdf, or https://www.ruby-lang.org."),
    "bundle": ((("bundle", "--version"),), "`gem install bundler`."),
    "java": ((("java", "-version"),), "Install a JDK, e.g. from https://adoptium.net."),
    "mvn": ((("mvn", "-v"),), "Install Maven: https://maven.apache.org."),
    "gradle": ((("gradle", "-v"),), "Use the repo's ./gradlew, or install Gradle."),
    "composer": ((("composer", "--version"),), "See https://getcomposer.org."),
    "make": ((("make", "--version"),), "Linux/macOS: install build tools. Windows: use WSL, "
             "or `choco install make`."),
    "just": ((("just", "--version"),), "See https://github.com/casey/just#installation."),
    "docker": ((("docker", "--version"),), "Install Docker Desktop: https://docs.docker.com."),
    "git": ((("git", "--version"),), "Install it from https://git-scm.com."),
}
COMMAND_TOOLS = {"npx": "npm", "pnpx": "pnpm", "python3": "python", "py": "python",
                 "pytest": "python", "bundler": "bundle", "./gradlew": None, "gradlew": None}


# --- version constraints (npm and PEP 440 styles) -------------------------------------------

def parse_version(text: str | None) -> tuple[int, ...] | None:
    """The first X[.Y[.Z]] in text ('v20.11.0', 'Python 3.12.1', 'go1.22'), or None."""
    m = VERSION_RE.search(text or "")
    if not m:
        return None
    return tuple(int(p) for p in m.groups() if p is not None)


def _pad(v: tuple[int, ...]) -> tuple[int, int, int]:
    return (tuple(v) + (0, 0, 0))[:3]  # type: ignore[return-value]


def _comparator(token: str) -> list[tuple[str, tuple[int, int, int]]] | None:
    """One constraint token -> (op, version) pairs that must all hold."""
    m = re.fullmatch(r"(\^|~=|~|>=|<=|==|!=|>|<|=)?v?([\d.]+(?:\.[x*])?|[x*])(\.\*)?", token)
    if not m:
        return None
    op, raw = m.group(1) or "", m.group(2) + (m.group(3) or "")
    parts = [p for p in raw.split(".") if p]
    nums = tuple(int(p) for p in parts if p.isdigit())
    wildcard = len(nums) < len(parts) or (not op and len(nums) < 3)
    if not nums:
        return []  # "*" or "x": anything
    low = _pad(nums)
    if op == "^":
        high = (low[0] + 1, 0, 0) if low[0] else (0, low[1] + 1, 0)
        return [(">=", low), ("<", high)]
    if op == "~":
        high = (low[0], low[1] + 1, 0) if len(nums) >= 2 else (low[0] + 1, 0, 0)
        return [(">=", low), ("<", high)]
    if op == "~=":  # PEP 440: ~=3.11 means >=3.11,<4 ; ~=3.11.2 means >=3.11.2,<3.12
        bump = nums[:-1]
        high = _pad(bump[:-1] + (bump[-1] + 1,)) if len(bump) else (low[0] + 1, 0, 0)
        return [(">=", low), ("<", high)]
    if (op in ("", "=", "==")) and wildcard:  # 18, 18.x, 3.11, ==3.11.*: a prefix
        high = list(low[:len(nums)])
        high[-1] += 1
        return [(">=", low), ("<", _pad(tuple(high)))]
    return [(op or "==", low)]


def satisfies(version: tuple[int, ...] | None, spec: str) -> bool | None:
    """Does version meet spec? None if either can't be read (e.g. 'lts/*')."""
    if version is None:
        return None
    v = _pad(version)
    # Aliases ("lts/*", "latest", "stable") and hyphen ranges: can't tell.
    if re.search(r"[^\d\s.xX*^~=<>!,v|]", spec):
        return None
    results = []
    for alternative in spec.split("||"):
        tokens = re.findall(r"(?:\^|~=|~|>=|<=|==|!=|>|<|=)?\s*v?[\dx*][\w.*]*", alternative)
        pairs = []
        for token in tokens:
            parsed = _comparator(token.replace(" ", ""))
            if parsed is None:
                return None
            pairs += parsed
        if not tokens:
            return None
        ok = all({">=": v >= w, "<=": v <= w, ">": v > w, "<": v < w, "==": v == w,
                  "!=": v != w}[op] for op, w in pairs)
        results.append(ok)
    return any(results)


# --- what the repo asks for --------------------------------------------------------------------

def _req(tool: str, spec: str, source: str, kind: str = "range") -> dict[str, str]:
    """kind: 'range' must hold; 'pin' is the version the maintainers use (a mismatch warns)."""
    return {"tool": tool, "required": spec.strip(), "source": source, "kind": kind}


def requirements(files: dict[str, str], paths: set[str] | None = None) -> list[dict[str, str]]:
    """Version requirements and needed tools, each with the file it came from."""
    paths = paths if paths is not None else set(files)
    reqs: list[dict[str, str]] = []
    pkg: dict[str, Any] = {}
    if "package.json" in files:
        try:
            pkg = json.loads(files["package.json"])
        except ValueError:
            pkg = {}
    for tool, spec in (pkg.get("engines") or {}).items():
        if tool in ("node", "npm", "pnpm", "yarn") and isinstance(spec, str):
            reqs.append(_req(tool, spec, "package.json (engines)"))
    if isinstance(pkg.get("packageManager"), str) and "@" in pkg["packageManager"]:
        name, _, version = pkg["packageManager"].partition("@")
        reqs.append(_req(name, version.split("+")[0], "package.json (packageManager)", "pin"))
    if isinstance((pkg.get("volta") or {}).get("node"), str):
        reqs.append(_req("node", pkg["volta"]["node"], "package.json (volta)", "pin"))
    for name, tool in ((".nvmrc", "node"), (".node-version", "node"),
                       (".python-version", "python"), (".ruby-version", "ruby"),
                       (".java-version", "java")):
        if name in files and (spec := files[name].strip().splitlines()[0:1]):
            if parse_version(spec[0]):
                reqs.append(_req(tool, spec[0].removeprefix("v"), name, "pin"))
    for line in files.get(".tool-versions", "").splitlines():
        parts = line.split()
        tool = {"nodejs": "node", "python": "python", "ruby": "ruby", "golang": "go",
                "rust": "rustc", "java": "java"}.get(parts[0] if parts else "")
        if tool and len(parts) > 1:
            reqs.append(_req(tool, parts[1], ".tool-versions", "pin"))
    if m := re.search(r"python-(\d[\d.]*)", files.get("runtime.txt", "")):
        reqs.append(_req("python", m.group(1), "runtime.txt", "pin"))
    if "pyproject.toml" in files:
        try:
            py = tomllib.loads(files["pyproject.toml"])
        except tomllib.TOMLDecodeError:
            py = {}
        spec = (py.get("project") or {}).get("requires-python") or (
            ((py.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}).get("python")
        if isinstance(spec, str):
            reqs.append(_req("python", spec, "pyproject.toml (requires-python)"))
    if m := re.search(r"python_requires\s*=\s*['\"]([^'\"]+)", files.get("setup.py", "")):
        reqs.append(_req("python", m.group(1), "setup.py (python_requires)"))
    if m := re.search(r"(?m)^go\s+(\d[\d.]*)", files.get("go.mod", "")):
        reqs.append(_req("go", ">=" + m.group(1), "go.mod"))
    for name in ("rust-toolchain.toml", "rust-toolchain"):
        if m := re.search(r"(\d+\.\d+(?:\.\d+)?)", files.get(name, "")):
            reqs.append(_req("rustc", m.group(1), name, "pin"))
    if m := re.search(r"rust-version\s*=\s*\"([\d.]+)\"", files.get("Cargo.toml", "")):
        reqs.append(_req("rustc", ">=" + m.group(1), "Cargo.toml (rust-version)"))
    if m := re.search(r"(?m)^ruby\s+['\"]([^'\"]+)['\"]", files.get("Gemfile", "")):
        reqs.append(_req("ruby", m.group(1), "Gemfile"))

    # Tools the documented commands use ("npm test" needs npm), without a version.
    stack = detect_stack(paths, {p: t for p, t in files.items() if p in STACK_FILES})
    have = {r["tool"] for r in reqs}
    for item in stack["install_commands"] + stack["test_commands"] + stack["lint_commands"]:
        first = item["command"].split()[0]
        tool = COMMAND_TOOLS.get(first, first)
        if tool in TOOLS and tool not in have:
            reqs.append(_req(tool, "", item["source"], "tool"))
            have.add(tool)
    for tool, implied in (("npm", "node"), ("pnpm", "node"), ("yarn", "node"), ("uv", "python"),
                          ("poetry", "python"), ("pipenv", "python"), ("pip", "python"),
                          ("cargo", "rustc"), ("bundle", "ruby"), ("tox", "python"),
                          ("nox", "python"), ("pre-commit", "python")):
        if tool in have and implied not in have:
            reqs.append(_req(implied, "", f"needed by {tool}", "tool"))
            have.add(implied)
    if "git" not in have:
        reqs.append(_req("git", "", "needed to contribute", "tool"))
    return reqs


# --- what this machine has -------------------------------------------------------------------

def installed_version(tool: str) -> tuple[str | None, tuple[int, ...] | None]:
    """(raw output line, parsed version) from running the tool, or (None, None) if absent."""
    for cmd in TOOLS[tool][0]:
        exe = shutil.which(cmd[0])
        if not exe:
            continue
        try:
            proc = subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, timeout=20,
                                  encoding="utf-8", errors="replace")
        except (OSError, subprocess.TimeoutExpired):
            continue
        out = (proc.stdout + proc.stderr).strip()
        if proc.returncode == 0 or out:
            # `go version` prints "go version go1.22.1 ..."; skip words to the first number.
            line = out.splitlines()[0] if out else ""
            return line[:120], parse_version(line.replace("go version go", ""))
    return None, None


def assess(req: dict[str, str], line: str | None, version: tuple[int, ...] | None) -> dict:
    """One requirement checked against what is installed."""
    tool, spec = req["tool"], req["required"]
    fix = TOOLS.get(tool, ((), ""))[1]
    out = {**req, "installed": ".".join(map(str, version)) if version else line, "fix": ""}
    if line is None:
        return {**out, "status": "missing", "fix": fix}
    if not spec:
        return {**out, "status": "pass"}
    if req["kind"] == "pin":  # the maintainers' version: same major (minor for Python) is fine
        want = parse_version(spec)
        if want is None or version is None:
            return {**out, "status": "unknown"}
        depth = 2 if tool in ("python", "ruby") else 1
        if version[:depth] == want[:depth]:
            return {**out, "status": "pass"}
        hint = {"node": "`nvm use`", "pnpm": "`corepack enable`", "yarn": "`corepack enable`",
                "python": "`pyenv install` or `uv python install`"}.get(
                    tool, "your version manager")
        return {**out, "status": "warn",
                "fix": f"The maintainers use {spec}; switch with {hint} if setup misbehaves."}
    ok = satisfies(version, spec)
    if ok is None:
        return {**out, "status": "unknown"}
    return {**out, "status": "pass" if ok else "fail",
            "fix": "" if ok else f"Need {tool} {spec}. {fix}"}


def read_repo_files(root: Path) -> tuple[dict[str, str], set[str]]:
    """The version and stack files at the root of a local clone."""
    files = {}
    for name in (*STACK_FILES, *VERSION_FILES):
        p = root / name
        if p.is_file():
            files[name] = p.read_text(encoding="utf-8", errors="replace")[:100_000]
    workflows = root / ".github" / "workflows"
    if workflows.is_dir():
        for p in sorted(workflows.glob("*.y*ml"))[:6]:
            files[f".github/workflows/{p.name}"] = p.read_text(encoding="utf-8", errors="replace")
    paths = set(files) | {p.name for p in root.iterdir()}
    return files, paths


def doctor(path: str = ".") -> dict[str, Any]:
    """Compare a local clone's requirements with this machine. See module docstring."""
    root = Path(path).resolve()
    files, paths = read_repo_files(root)
    reqs = requirements(files, paths)
    checks, cache = [], {}
    for req in reqs:
        if req["tool"] not in cache:
            cache[req["tool"]] = installed_version(req["tool"])
        checks.append(assess(req, *cache[req["tool"]]))
    # One line per tool: the strictest result wins.
    order = {"missing": 0, "fail": 1, "warn": 2, "unknown": 3, "pass": 4}
    checks.sort(key=lambda c: (order[c["status"]], c["tool"]))
    summary = {s: sum(c["status"] == s for c in checks) for s in order}
    return {"path": str(root), "checks": checks, "summary": summary,
            "ready": summary["missing"] == 0 and summary["fail"] == 0}


def to_markdown(result: dict[str, Any]) -> str:
    lines = [f"# Setup doctor: `{result['path']}`", ""]
    for c in result["checks"]:
        need = f" {c['required']}" if c["required"] else ""
        have = f" (you have {c['installed']})" if c["installed"] else ""
        fix = f" — {c['fix']}" if c["fix"] else ""
        icon = ICON[c["status"]]
        lines.append(f"- {icon} **{c['tool']}{need}**{have}, from {c['source']}{fix}")
    verdict = "Ready to set up." if result["ready"] else "Install or update the ❌ items first."
    lines += ["", verdict]
    return "\n".join(lines)


def main() -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--path", default=".", help="your local clone (default: current folder)")
    ap.add_argument("--markdown", action="store_true", help="print Markdown instead of JSON")
    args = ap.parse_args()
    result = doctor(args.path)
    emit_text(to_markdown(result)) if args.markdown else emit_json(result)
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    sys.exit(run_cli(main))
