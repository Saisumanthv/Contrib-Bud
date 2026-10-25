"""Retrieval for the repo and issue chats (the "R" in RAG).

A repo is downloaded once as a tarball, its text files are cut into overlapping line windows,
and the windows ("chunks") are ranked against each question with BM25, a keyword-relevance
score. The best chunks go into the chat prompt with their file and line numbers, so answers can
cite real code.

Why BM25 rather than embeddings: indexing needs no API calls (the free Gemini embedding quota
would take minutes for a mid-sized repo), it works offline with Ollama, and questions about
code are mostly about identifiers and file names, which keyword matching handles well.
"""

from __future__ import annotations

import heapq
import io
import math
import re
import tarfile
import threading
import time
from collections import Counter, OrderedDict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from urllib.parse import quote

import requests

from buddy.skill import GitHubError, github

MAX_ARCHIVE_BYTES = 60 * 1024 * 1024  # refuse to download bigger repos
MAX_FILE_BYTES = 200 * 1024           # skip bigger files (generated data, bundles)
MAX_INDEX_BYTES = 12 * 1024 * 1024    # stop indexing after this much text
CHUNK_LINES = 40
CHUNK_STEP = 30                       # 10 lines shared by neighbouring chunks
CACHE_SIZE = 4                        # repos kept in memory
CACHE_TTL = 30 * 60
SNIPPET_BUDGET = 14000                # characters of retrieved code per prompt
BM25_K1, BM25_B = 1.2, 0.75

SKIP_DIRS = re.compile(r"(^|/)(node_modules|vendor|dist|build|third_party|\.git|__pycache__|"
                       r"__snapshots__|\.venv|venv|coverage|\.next|target)/")
SKIP_FILES = re.compile(r"(lock\.json|\.lock|-lock\.yaml|\.min\.(js|css)|\.map|\.svg)$", re.I)
# Legal text matches everyday words in questions ("useful", "information") and never answers them.
LEGAL_FILES = re.compile(r"(^|/)(LICEN[CS]E|COPYING|NOTICE|PATENTS)(\.[a-z]+)?$", re.I)
BINARY_EXT = re.compile(r"\.(png|jpe?g|gif|ico|webp|bmp|pdf|zip|gz|tgz|bz2|xz|7z|jar|war|class|"
                        r"so|dll|exe|bin|o|a|pyc|woff2?|ttf|otf|eot|mp[34]|mov|avi|wav|ogg|"
                        r"sqlite|db|psd|xo)$", re.I)
STOPWORDS = frozenset(
    "the and for are but not you all any can her was one our out has have this that with from "
    "they will would there their what when which who how why does did into than then them these "
    "those its it's also just about some more most other such only own same very use used using "
    "get set let var const def return self none null true false import from".split()
)


# Words people use for parts of a project, mapped to the names those parts usually have in code
# ("landing page" is index.html). Keyword search can't make these leaps on its own.
RELATED = {
    "landing": ("index", "home", "main"), "homepage": ("index", "home", "main"),
    "home": ("index", "main"), "front": ("index", "home"),
    "page": ("html", "template", "view"), "screen": ("view", "page", "component"),
    "style": ("css", "scss", "theme"), "styled": ("css", "scss", "theme"),
    "styling": ("css", "scss", "theme"), "look": ("css", "style"), "layout": ("css", "html"),
    "design": ("css", "html", "template"), "logo": ("img", "brand", "header"),
    "navbar": ("navigation", "nav", "header"), "menu": ("navigation", "nav"),
    "button": ("btn",), "test": ("tests", "spec"), "tests": ("test", "spec"),
    "setup": ("install", "requirements", "config"), "install": ("setup", "requirements"),
    "config": ("settings", "configuration", "yml", "yaml"), "settings": ("config",),
    "database": ("db", "model", "schema"), "api": ("routes", "endpoint", "handler"),
    "docs": ("readme", "documentation"), "ci": ("workflows", "github"),
}


@dataclass(frozen=True)
class Chunk:
    path: str
    start: int  # 1-based, inclusive
    end: int
    text: str


def tokenize(text: str) -> list[str]:
    """Lower-cased words; identifiers also split into parts (getUserName -> get, user, name)."""
    tokens = []
    for raw in re.findall(r"[A-Za-z0-9_]+", text):
        parts = [p for p in re.split(r"_+|(?<=[a-z0-9])(?=[A-Z])", raw) if p]
        if len(parts) > 1:
            tokens.append(raw.lower())
        tokens.extend(p.lower() for p in parts)
    return [t for t in tokens if len(t) > 1 and t not in STOPWORDS]


def expand(tokens: list[str]) -> list[str]:
    """The tokens plus their RELATED names."""
    return tokens + [r for t in tokens for r in RELATED.get(t, ())]


def chunk_text(path: str, text: str) -> Iterator[Chunk]:
    """Overlapping windows of CHUNK_LINES lines, the last one ending at the file's end."""
    lines = text.splitlines()
    start = 0
    while start < len(lines):
        window = lines[start:start + CHUNK_LINES]
        if any(line.strip() for line in window):
            yield Chunk(path, start + 1, start + len(window), "\n".join(window))
        if start + CHUNK_LINES >= len(lines):
            break
        start += CHUNK_STEP


@dataclass
class RepoIndex:
    repo: str
    ref: str
    chunks: list[Chunk]
    files: int
    truncated: bool
    built_at: float = field(default_factory=time.time)
    _postings: dict[str, list[tuple[int, int]]] = field(default_factory=dict, repr=False)
    _lengths: list[int] = field(default_factory=list, repr=False)

    @classmethod
    def build(cls, repo: str, ref: str, files: Iterable[tuple[str, str]],
              truncated: bool = False) -> RepoIndex:
        index = cls(repo, ref, [], 0, truncated)
        for path, text in files:
            index.files += 1
            # File names count twice: "where is the landing page" should find landing.html.
            path_tokens = tokenize(path.replace("/", " ").replace(".", " ")) * 2
            for chunk in chunk_text(path, text):
                counts = Counter(tokenize(chunk.text) + path_tokens)
                number = len(index.chunks)
                index.chunks.append(chunk)
                index._lengths.append(sum(counts.values()))
                for token, tf in counts.items():
                    index._postings.setdefault(token, []).append((number, tf))
        return index

    def search(self, query: str, k: int = 6, context: str = "") -> list[Chunk]:
        """Top-k chunks by BM25, skipping near-duplicates (overlapping windows of one file).

        `context` (e.g. an issue's description) also counts, at half the weight of the query.
        """
        if not self.chunks:
            return []
        n = len(self.chunks)
        avg = sum(self._lengths) / n
        weights = (dict.fromkeys(expand(tokenize(context)), 0.5)
                   | dict.fromkeys(expand(tokenize(query)), 0.6)  # related terms count less
                   | dict.fromkeys(tokenize(query), 1.0))
        scores: dict[int, float] = {}
        for token, weight in weights.items():
            postings = self._postings.get(token)
            if not postings:
                continue
            idf = math.log(1 + (n - len(postings) + 0.5) / (len(postings) + 0.5))
            for number, tf in postings:
                norm = tf + BM25_K1 * (1 - BM25_B + BM25_B * self._lengths[number] / avg)
                scores[number] = scores.get(number, 0.0) + weight * idf * tf * (BM25_K1 + 1) / norm

        picked: list[Chunk] = []
        for number in heapq.nlargest(k * 3, scores, key=scores.get):
            chunk = self.chunks[number]
            if any(p.path == chunk.path and p.start <= chunk.end and chunk.start <= p.end
                   for p in picked):
                continue
            picked.append(chunk)
            if len(picked) == k:
                break
        return picked

    def stats(self) -> dict[str, object]:
        return {"repo": self.repo, "branch": self.ref, "files": self.files,
                "chunks": len(self.chunks), "truncated": self.truncated}


# --- download -----------------------------------------------------------------------

def download_archive(owner: str, repo: str, ref: str) -> bytes:
    """The repo at `ref` as a .tar.gz (one GitHub API call, then a redirect to codeload)."""
    url = f"{github.API}/repos/{owner}/{repo}/tarball/{quote(ref, safe='')}"
    try:
        resp = requests.get(url, headers=github._headers(), stream=True,
                            timeout=(github.CONNECT_TIMEOUT, 60))
    except requests.RequestException as exc:
        raise GitHubError(f"Could not download {owner}/{repo} ({exc.__class__.__name__}).") from exc
    with resp:
        if resp.status_code == 404:
            raise GitHubError(f"Could not download {owner}/{repo} at '{ref}'.")
        if resp.status_code in (403, 429):
            raise GitHubError(github._rate_limit_message(resp))
        if resp.status_code >= 400:
            raise GitHubError(f"GitHub returned {resp.status_code} downloading {owner}/{repo}.")
        data = bytearray()
        for block in resp.iter_content(1 << 16):
            data += block
            if len(data) > MAX_ARCHIVE_BYTES:
                raise GitHubError(f"{owner}/{repo} is too large to index for chat "
                                  f"(over {MAX_ARCHIVE_BYTES // (1024 * 1024)} MB).")
    return bytes(data)


def iter_repo_files(archive: bytes) -> tuple[list[tuple[str, str]], bool]:
    """(path, text) for each indexable text file, and whether MAX_INDEX_BYTES cut it short.

    Nothing is written to disk, so archive paths can't escape anywhere.
    """
    files: list[tuple[str, str]] = []
    total = 0
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for member in tar:
            if not member.isfile() or member.size > MAX_FILE_BYTES:
                continue
            path = member.name.split("/", 1)[-1]  # drop the "owner-repo-sha/" folder
            if (SKIP_DIRS.search(path) or SKIP_FILES.search(path) or BINARY_EXT.search(path)
                    or LEGAL_FILES.search(path)):
                continue
            extracted = tar.extractfile(member)
            raw = extracted.read() if extracted else b""
            if not raw.strip() or b"\0" in raw[:4096]:  # empty or binary
                continue
            if total + len(raw) > MAX_INDEX_BYTES:
                return files, True
            total += len(raw)
            files.append((path, raw.decode("utf-8", errors="replace")))
    return files, False


# --- cache ----------------------------------------------------------------------------

_cache: OrderedDict[tuple[str, str], RepoIndex] = OrderedDict()
_cache_lock = threading.Lock()
_build_locks: dict[tuple[str, str], threading.Lock] = {}


def _fresh(key: tuple[str, str]) -> RepoIndex | None:
    index = _cache.get(key)
    if index and time.time() - index.built_at < CACHE_TTL:
        _cache.move_to_end(key)
        return index
    return None


def get_index(owner: str, repo: str, ref: str) -> RepoIndex:
    """The cached index for a repo, building it (once, even under concurrent requests)."""
    key = (f"{owner}/{repo}".lower(), ref)
    with _cache_lock:
        if index := _fresh(key):
            return index
        build_lock = _build_locks.setdefault(key, threading.Lock())
    with build_lock:
        with _cache_lock:  # another request may have finished building while we waited
            if index := _fresh(key):
                return index
        files, truncated = iter_repo_files(download_archive(owner, repo, ref))
        index = RepoIndex.build(f"{owner}/{repo}", ref, files, truncated)
        with _cache_lock:
            _cache[key] = index
            _cache.move_to_end(key)
            while len(_cache) > CACHE_SIZE:
                _cache.popitem(last=False)
        return index


def format_snippets(chunks: list[Chunk], budget: int = SNIPPET_BUDGET) -> str:
    """Chunks as prompt text, each headed [path:start-end], cut to `budget` characters."""
    parts, used = [], 0
    for c in chunks:
        part = f"[{c.path}:{c.start}-{c.end}]\n```\n{c.text}\n```"
        if used + len(part) > budget:
            break
        parts.append(part)
        used += len(part)
    return "\n\n".join(parts) or "(nothing relevant was found in the repository's files)"
