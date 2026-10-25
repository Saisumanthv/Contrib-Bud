"""Check the citations in a chat answer against the repository and the facts the model saw.

The prompts require sources like [src/app.py:12-40], [README.md], [#123] or
[comment by @name]. For each one this confirms the file exists, the lines exist,
and the sentence citing them is actually about what is in those lines: shared
words, and any `quoted code` or code block found there verbatim.

Statuses: "verified" (source exists and backs the claim), "unsupported" (source
exists but does not seem to say that), "not_found" (no such file, line or
issue) and "unchecked" (nothing to check against, e.g. code search was down).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from buddy.rag import RepoIndex, tokenize

CITATION_RE = re.compile(r"\[([^\[\]\n]{1,200})\](?!\()")  # not a Markdown link's text
FILE_REF_RE = re.compile(
    r"^`?(?P<path>[\w.@+/-]+?)`?(?::L?(?P<start>\d+)(?:\s*[-–]\s*L?(?P<end>\d+))?)?$")
EXTENSIONLESS = re.compile(r"(^|/)(Makefile|Dockerfile|Justfile|Procfile|Gemfile|LICENSE|"
                           r"CODEOWNERS|README|CONTRIBUTING)$", re.I)
ISSUE_REF_RE = re.compile(r"^(?:issue\s+|PR\s+)?#(\d+)$", re.I)
COMMENT_REF_RE = re.compile(r"^comment by @([\w-]+)$", re.I)
FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.S)
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
PATHLIKE_RE = re.compile(r"^[\w.@+-]*/[\w.@+/-]*$|\.(py|pyi|js|jsx|mjs|ts|tsx|md|rst|txt|json|"
                         r"ya?ml|toml|cfg|ini|html?|css|scss|go|rs|java|kt|rb|php|c|h|cpp|sh)$")
LINE_SLACK = 3        # cited ranges are often a few lines off
MIN_SHARED = 2        # claim words that must appear in the cited lines (1 for short claims)...
MIN_SHARE = 0.25      # ...and the share of the claim's words they must make up
CODE_MATCH = 0.6      # share of a quoted code block's lines that must be in the source
# Everyday words and words that say where something is, not what is claimed. A claim
# made only of these ("It lives in [app.py:3]") is checked for the source existing.
CLAIM_STOPWORDS = frozenset(
    "file files line lines function functions method class defined define defines see here "
    "shown show shows uses use called call calls code repo repository project example "
    "located found contains contain includes include part section run runs "
    "it is in on of to at by be as or an we if so do is am was were been being has had "
    "lives live living where look looking check find there you your yours can could should "
    "need needs want wants change changes edit update add like which what who whom whose "
    "here this that thing things way ways may might must way also then there's it's "
    "happens happen handled handles handle done start starts started begin begins "
    "inside within under over near top bottom first last next mainly mostly main "
    "since because working work fix fixing related relevant specific specifically".split())
SEVERITY = {"verified": 0, "unchecked": 1, "unsupported": 2, "not_found": 3}


@dataclass
class Known:
    """What else the model was given: the issue it was asked about, other issues and PRs
    in its facts, who commented, and commands contrib-buddy derived from a file (e.g.
    "uv run pytest" from pyproject.toml), which count as that file's content."""
    issue_numbers: set[int] | None = None  # None: no issue data (repo chat)
    comment_authors: set[str] = field(default_factory=set)
    derived: dict[str, list[str]] = field(default_factory=dict)  # path -> facts from it
    # Guides (the codebase tour) cite whole files to say "go and read this", not "this
    # file says so": then a whole-file citation only has to exist.
    navigational: bool = False
    skip: set[str] = field(default_factory=set)  # bracketed text that isn't a source

    @classmethod
    def from_stack(cls, stack: dict[str, Any], **kwargs: Any) -> Known:
        derived: dict[str, list[str]] = {}
        for key in ("install_commands", "test_commands", "lint_commands", "ci_commands"):
            for item in stack.get(key) or []:
                derived.setdefault(item["source"], []).append(item["command"])
        return cls(derived=derived, **kwargs)


# --- finding citations and their claims ---------------------------------------------

def _code_spans(text: str) -> list[tuple[int, int]]:
    return [m.span() for m in FENCE_RE.finditer(text)] + [
        m.span() for m in INLINE_CODE_RE.finditer(text)]


def _inside(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in spans)


def _sentences(text: str) -> list[tuple[int, int]]:
    """(start, end) of each sentence or line, outside code blocks."""
    bounds, start = [], 0
    for m in re.finditer(r"(?<=[.!?])\s+|\n", text):
        bounds.append((start, m.start()))
        start = m.end()
    bounds.append((start, len(text)))
    return [b for b in bounds if text[b[0]:b[1]].strip()]


def claim_for(text: str, pos: int) -> tuple[str, list[str]]:
    """(the sentence around a citation, code blocks it introduces) with citations removed.

    A citation standing alone after a sentence ("... the server. [app.py:3]") is about
    the sentence before it; one right after a code block is about that code.
    """
    fences = list(FENCE_RE.finditer(text))
    before = text[:pos].rstrip()
    if block := next((f for f in fences if f.end() == len(before)), None):
        intro = [s for s in _sentences(text[:block.start()]) if s[1] <= block.start()]
        claim = CITATION_RE.sub(" ", text[slice(*intro[-1])]) if intro else ""
        return _tidy(claim), [block.group(1)]
    fences = [f.span() for f in fences]
    sentences = [s for s in _sentences(text) if not _inside(s[0], fences)]
    here = next((i for i, (s, e) in enumerate(sentences) if s <= pos <= e), None)
    if here is None:
        return "", []
    start, end = sentences[here]
    # With several citations in a sentence, each backs the clause just before it.
    earlier = [m.end() for m in CITATION_RE.finditer(text, start, pos)]
    clause = CITATION_RE.sub(" ", text[earlier[-1] if earlier else start:pos])
    claim = clause if len(tokenize(clause)) >= 2 else CITATION_RE.sub(" ", text[start:end])
    if len(tokenize(claim)) < 2 and here > 0:
        claim = CITATION_RE.sub(" ", text[slice(*sentences[here - 1])]) + " " + claim
    blocks = []
    rest = CITATION_RE.sub(" ", text[pos:end])
    if rest.rstrip().endswith(":"):  # "In [app.py:3-9]:" followed by the code
        claim += rest
        after = end + len(text[end:]) - len(text[end:].lstrip())
        if follow := FENCE_RE.match(text, after):
            blocks.append(follow.group(1))
    return _tidy(claim), blocks


def _tidy(text: str) -> str:
    """Close the gaps left where citations were cut out (leaving `code` as it is)."""
    parts = re.split(r"(`[^`\n]*`)", re.sub(r"\s+", " ", text))
    return "".join(p if i % 2 else re.sub(r"\s+([.,;:!?])", r"\1", p)
                   for i, p in enumerate(parts)).strip(" ,;")


def parse_ref(part: str) -> dict[str, Any] | None:
    """One source inside a citation's brackets, or None if it isn't one we know."""
    part = part.strip()
    if m := ISSUE_REF_RE.match(part):
        return {"kind": "issue", "text": part, "number": int(m.group(1))}
    if m := COMMENT_REF_RE.match(part):
        return {"kind": "comment", "text": part, "author": m.group(1)}
    m = FILE_REF_RE.match(part)
    if m and ("." in m["path"] or "/" in m["path"] or EXTENSIONLESS.search(m["path"])) \
            and not m["path"].endswith(".") and not part.replace(".", "").isdigit():
        start = int(m["start"]) if m["start"] else None
        end = int(m["end"]) if m["end"] else start
        return {"kind": "file", "text": part, "path": m["path"], "start": start, "end": end}
    return None


# --- checking -------------------------------------------------------------------------

def _norm(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def stem(word: str) -> str:
    """Crude stemming so "users"/"user" and "committed"/"commit" match."""
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[:-len(suffix)]
            break
    if len(word) >= 4 and word[-1] == word[-2] and word[-1] not in "aeiou":
        word = word[:-1]
    return word[:-1] if len(word) > 3 and word.endswith("e") else word


def _words(text: str) -> set[str]:
    return {stem(t) for t in tokenize(text)}


def support(claim: str, blocks: list[str], source: list[str],
            ignore: set[str]) -> tuple[bool, str]:
    """(does `source` back the claim?, why not)."""
    text = "\n".join(source)
    flat = _norm(text)
    for block in blocks:
        lines = [_norm(line) for line in block.splitlines() if _norm(line)]
        if lines and sum(line in flat for line in lines) / len(lines) < CODE_MATCH:
            return False, "the quoted code is not in those lines"
    quotes = [q for q in INLINE_CODE_RE.findall(claim)  # file names aren't code to look for
              if not PATHLIKE_RE.search(q) and not _words(q) <= ignore and len(q) <= 80]
    missing = [q for q in quotes if _norm(q) not in flat and q.strip("()") not in text]
    if missing:
        return False, f"`{missing[0]}` is not in those lines"
    if quotes or blocks:
        return True, ""
    words = {stem(t) for t in tokenize(claim) if t not in CLAIM_STOPWORDS} - ignore
    if not words:
        return True, ""
    shared = words & _words(text)
    needed = 1 if len(words) <= 3 else MIN_SHARED
    if len(shared) >= needed and len(shared) / len(words) >= MIN_SHARE:
        return True, ""
    absent = [t for t in tokenize(claim) if stem(t) in words - shared]
    return False, "those lines don't mention " + ", ".join(dict.fromkeys(absent[:3]))


def _best_window(claim: str, blocks: list[str], lines: list[str], ignore: set[str],
                 size: int) -> tuple[int, int] | None:
    """Where in the file the claim does match, when the cited lines don't."""
    size = max(5, min(size, 60))
    for start in range(0, max(1, len(lines) - size + 1), max(1, size // 2)):
        if support(claim, blocks, lines[start:start + size], ignore)[0]:
            return start + 1, min(len(lines), start + size)
    return None


def check_file(ref: dict[str, Any], claim: str, blocks: list[str],
               index: RepoIndex | None, known: Known | None = None) -> dict[str, Any]:
    if index is None:
        return {**ref, "status": "unchecked", "reason": "code search is unavailable"}
    path = index.resolve(ref["path"])
    if path is None:
        folder = ref["path"].strip("/") + "/"
        if ref["start"] is None and any(p.startswith(folder) for p in index.lines):
            return {**ref, "kind": "folder", "path": folder.rstrip("/"), "status": "verified",
                    "reason": ""}  # a folder, e.g. [src/cli/]: it exists
        return {**ref, "status": "not_found", "reason": f"no file {ref['path']} in the repo"}
    lines = index.lines[path]
    derived = [fact for source, facts in (known.derived if known else {}).items()
               if index.resolve(source) == path for fact in facts]
    ref = {**ref, "path": path}
    ignore = _words(path.replace("/", " ").replace(".", " "))
    start, end = ref["start"], ref["end"]
    if start is None and known and known.navigational:
        return {**ref, "status": "verified", "reason": ""}
    if start is None:
        ok, why = support(claim, blocks, lines + derived, ignore)
        return {**ref, "status": "verified" if ok else "unsupported",
                "reason": "" if ok else f"the file doesn't seem to say that: {why}"}
    if start > len(lines) or end < start:
        return {**ref, "status": "not_found",
                "reason": f"{path} has only {len(lines)} lines" if start > len(lines)
                else "the line range is backwards"}
    window = lines[max(0, start - 1 - LINE_SLACK):min(len(lines), end + LINE_SLACK)]
    ok, why = support(claim, blocks, window + derived, ignore)
    if ok:
        return {**ref, "status": "verified", "reason": ""}
    found = _best_window(claim, blocks, lines, ignore, end - start + 1)
    if found:
        return {**ref, "status": "unsupported", "suggested": {"start": found[0], "end": found[1]},
                "reason": f"{why}; it matches lines {found[0]}-{found[1]} instead"}
    return {**ref, "status": "unsupported", "reason": why}


def check_ref(ref: dict[str, Any], claim: str, blocks: list[str], index: RepoIndex | None,
              known: Known | None) -> dict[str, Any]:
    if ref["kind"] == "file":
        return check_file(ref, claim, blocks, index, known)
    if known is None or known.issue_numbers is None:
        return {**ref, "status": "unchecked", "reason": "no issue data to check against"}
    if ref["kind"] == "issue":
        ok = ref["number"] in known.issue_numbers
        return {**ref, "status": "verified" if ok else "not_found",
                "reason": "" if ok else f"#{ref['number']} is not in the issue data the AI had"}
    authors = {a.lower() for a in known.comment_authors}
    ok = ref["author"].lower() in authors
    return {**ref, "status": "verified" if ok else "not_found",
            "reason": "" if ok else f"@{ref['author']} has not commented on this issue"}


def _utf16(text: str, pos: int) -> int:
    """A Python string index as a JavaScript (UTF-16) one, for the page to mark citations."""
    return len(text[:pos].encode("utf-16-le")) // 2


def verify(reply: str, index: RepoIndex | None, known: Known | None = None) -> list[dict]:
    """Every citation in `reply`, in order, with its sources checked.

    Each item: {"raw", "start", "end" (UTF-16 offsets of the brackets), "status" (the
    worst of its sources), "refs": [{"kind", "text", "status", "reason", ...}]}.
    """
    code = _code_spans(reply)
    out = []
    for m in CITATION_RE.finditer(reply):
        if _inside(m.start(), code):
            continue
        skip = {s.lower() for s in known.skip} if known else set()
        refs = [r for part in re.split(r"[,;]\s*", m.group(1))
                if part.strip().lower() not in skip and (r := parse_ref(part))]
        if not refs:
            continue  # "[x]" checkboxes, "[optional]" and the like
        claim, blocks = claim_for(reply, m.start())
        checked = [check_ref(r, claim, blocks, index, known) for r in refs]
        out.append({"raw": m.group(0), "start": _utf16(reply, m.start()),
                    "end": _utf16(reply, m.end()), "claim": claim,
                    "status": max((c["status"] for c in checked), key=SEVERITY.get),
                    "refs": checked})
    return out


def summary(citations: list[dict]) -> dict[str, int]:
    """How many citations ended up in each status."""
    counts = dict.fromkeys(SEVERITY, 0)
    for c in citations:
        counts[c["status"]] += 1
    return counts
