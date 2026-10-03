"""Shared GitHub helpers for contrib-buddy scripts.

Uses the GitHub REST API for metadata and raw.githubusercontent.com for file
contents (raw fetches do not count against the REST API rate limit).
Reads an optional GITHUB_TOKEN from the environment; never requires it.

Dependencies: Python 3.11+ standard library and `requests`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

try:
    import requests
except ImportError:  # pragma: no cover - environment guard
    sys.stderr.write("error: the 'requests' package is required. Run: pip install requests\n")
    sys.exit(2)

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
TIMEOUT = 20
# Give up on an unreachable address quickly so the next DNS address is tried (a
# blocked GitHub IP otherwise costs the OS default of ~21s per new connection).
CONNECT_TIMEOUT = 5
USER_AGENT = "contrib-buddy/0.1"
CACHE_DIR = Path(tempfile.gettempdir()) / "contrib-buddy-cache"
CACHE_TTL = 15 * 60

_REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
_ISSUE_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/([^/\s]+)/([^/\s]+)/(?:issues|pull)/(\d+)"
)


_local = threading.local()


def _session() -> requests.Session:
    """One keep-alive session per thread: reuses connections across the many small fetches."""
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
    return _local.session


class GitHubError(Exception):
    """A user-facing error with a friendly, actionable message."""


def parse_repo(value: str) -> tuple[str, str]:
    """Parse 'owner/repo' or a GitHub URL into (owner, repo).

    Raises GitHubError with a helpful message on malformed input.
    """
    v = value.strip().removesuffix("/").removesuffix(".git")
    v = re.sub(r"^(?:https?://)?(?:www\.)?github\.com/", "", v)
    parts = v.split("/")
    if len(parts) >= 2:
        v = f"{parts[0]}/{parts[1]}"
    if not _REPO_RE.match(v):
        raise GitHubError(
            f"'{value}' is not a valid repository. Use the form owner/repo, e.g. facebook/react."
        )
    owner, repo = v.split("/")
    return owner, repo


def parse_issue_url(url: str) -> tuple[str, str, int]:
    """Parse a GitHub issue URL into (owner, repo, number)."""
    m = _ISSUE_URL_RE.match(url.strip())
    if not m:
        raise GitHubError(
            f"'{url}' is not a GitHub issue URL. "
            "Expected something like https://github.com/owner/repo/issues/123."
        )
    return m.group(1), m.group(2), int(m.group(3))


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _rate_limit_message(resp: requests.Response) -> str:
    reset = resp.headers.get("X-RateLimit-Reset")
    wait = ""
    if reset and reset.isdigit():
        minutes = max(1, round((int(reset) - time.time()) / 60))
        wait = f" It resets in about {minutes} minute(s)."
    has_token = bool(os.environ.get("GITHUB_TOKEN", "").strip())
    hint = (
        "Your GITHUB_TOKEN quota is used up."
        if has_token
        else "Unauthenticated requests are limited to 60/hour. Set GITHUB_TOKEN "
        "(a fine-grained token with no extra permissions is enough) to get 5000/hour."
    )
    return f"GitHub API rate limit reached.{wait} {hint}"


def _cache_file(path: str, params: dict[str, Any] | None) -> Path | None:
    if os.environ.get("CONTRIB_BUDDY_NO_CACHE"):
        return None
    key = hashlib.sha256(json.dumps([path, params or {}], sort_keys=True).encode()).hexdigest()
    return CACHE_DIR / f"{key[:32]}.json"


def _cache_read(file: Path | None) -> Any:
    try:
        if file and file.exists() and time.time() - file.stat().st_mtime < CACHE_TTL:
            return json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return None


def _cache_write(file: Path | None, data: Any) -> None:
    if not file:
        return
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass  # caching is best-effort


def api_get(path: str, params: dict[str, Any] | None = None, *, not_found: str = "") -> Any:
    """GET a GitHub REST API path and return parsed JSON.

    Responses are cached on disk for 15 minutes to save rate limit
    (disable with CONTRIB_BUDDY_NO_CACHE=1).

    Args:
        path: API path starting with '/', e.g. '/repos/facebook/react'.
        params: Optional query parameters.
        not_found: Custom message for a 404 response.
    """
    cache_file = _cache_file(path, params)
    cached = _cache_read(cache_file)
    if cached is not None:
        return cached
    try:
        resp = _session().get(f"{API}{path}", headers=_headers(), params=params,
                              timeout=(CONNECT_TIMEOUT, TIMEOUT))
    except requests.RequestException as exc:
        raise GitHubError(f"Could not reach api.github.com ({exc.__class__.__name__}). "
                          "Check your internet connection.") from exc
    if resp.status_code == 404:
        raise GitHubError(not_found or f"Not found on GitHub: {path}")
    if resp.status_code in (403, 429) and (
        resp.headers.get("X-RateLimit-Remaining") == "0" or "rate limit" in resp.text.lower()
    ):
        raise GitHubError(_rate_limit_message(resp))
    if resp.status_code == 401:
        raise GitHubError("GITHUB_TOKEN was rejected (401). Check or unset it.")
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("message", "")
        except ValueError:
            detail = resp.text[:200]
        raise GitHubError(f"GitHub API error {resp.status_code} for {path}: {detail}")
    data = resp.json()
    _cache_write(cache_file, data)
    return data


def raw_get(owner: str, repo: str, ref: str, path: str) -> str | None:
    """Fetch a file's text from raw.githubusercontent.com, or None if missing."""
    url = f"{RAW}/{owner}/{repo}/{quote(ref, safe='')}/{quote(path)}"
    try:
        resp = _session().get(url, headers={"User-Agent": USER_AGENT},
                              timeout=(CONNECT_TIMEOUT, TIMEOUT))
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    resp.encoding = resp.encoding or "utf-8"
    return resp.text


def truncate(text: str | None, limit: int) -> tuple[str, bool]:
    """Return (text cut to `limit` chars, was_truncated)."""
    if text is None:
        return "", False
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n…[truncated]", True


def emit_json(data: Any) -> None:
    """Print JSON to stdout with UTF-8 safety on Windows consoles."""
    out = json.dumps(data, indent=2, ensure_ascii=False)
    sys.stdout.buffer.write(out.encode("utf-8") + b"\n")


def emit_text(text: str) -> None:
    """Print text to stdout as UTF-8."""
    sys.stdout.buffer.write(text.encode("utf-8") + b"\n")


def run_cli(main: Callable[[], int | None]) -> None:
    """Run a script's main(), turning GitHubError into a friendly stderr message."""
    try:
        code = main()
    except GitHubError as exc:
        sys.stderr.write(f"error: {exc}\n")
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
    sys.exit(code or 0)
