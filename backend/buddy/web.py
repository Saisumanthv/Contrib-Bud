"""`buddy web` — a local browser UI over the same facts + model pipeline as the CLI.

Standard library only: a threaded HTTP server bound to localhost that serves one
page (static/index.html) and three JSON endpoints mirroring analyze/issues/plan.
"""

from __future__ import annotations

import json
import sys
import threading
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from buddy import citations, llm, rag
from buddy.cli import analyze_facts, apply_refinement, issues_facts, merge_ranking, plan_facts
from buddy.providers import Provider, ProviderError, get_provider
from buddy.skill import GitHubError, github, issues, planner, ranker, repo_docs

CHAT_HISTORY_TURNS = 10  # messages of earlier conversation sent with each question

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
MAX_BODY = 10_000


class ApiError(Exception):
    """A user-facing error returned as JSON with an HTTP status."""

    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.status = status


# --- model helpers ----------------------------------------------------------------

def provider_status() -> dict[str, Any]:
    """Describe the configured model backend without failing."""
    try:
        provider = get_provider()
    except ProviderError as exc:
        return {"ai_available": False, "ai_problem": str(exc)}
    return {"ai_available": True, "provider": provider.name, "model": provider.model}


def maybe_provider(use_ai: bool) -> tuple[Provider | None, str | None]:
    """Return (provider, problem). The web UI degrades to facts-only instead of failing."""
    if not use_ai:
        return None, None
    try:
        return get_provider(), None
    except ProviderError as exc:
        return None, str(exc)


def _text(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ApiError(f"Missing '{key}'.")
    return value.strip()


# --- endpoints --------------------------------------------------------------------

def api_analyze(body: dict[str, Any]) -> dict[str, Any]:
    info = repo_docs.fetch_repo_docs(_text(body, "repo"))
    provider, ai_error = maybe_provider(bool(body.get("ai", True)))
    summary = None
    if provider:
        try:
            summary = llm.ask(provider, "analyze", repo=info["repo"],
                              facts=json.dumps(analyze_facts(info), ensure_ascii=False))
        except ProviderError as exc:
            ai_error = str(exc)
    return {"facts": info, "ai_summary": summary, "ai_error": ai_error,
            "model": provider.model if provider else None}


def api_issues(body: dict[str, Any]) -> dict[str, Any]:
    limit = body.get("limit", 8)
    if not isinstance(limit, int) or not 1 <= limit <= 30:
        raise ApiError("'limit' must be a number from 1 to 30.")
    found = issues.find_issues(_text(body, "repo"), limit, 14, True)
    ranked = ranker.rank_issues(found["issues"])
    provider, ai_error = maybe_provider(bool(body.get("ai", True)))
    ai = None
    if provider and ranked:
        try:
            ai = llm.ask_json(
                provider, "issues", llm.validate_ranking({i["number"] for i in ranked}),
                repo=found["repo"], facts=json.dumps(issues_facts(ranked), ensure_ascii=False),
            )
        except (ProviderError, llm.LLMError) as exc:
            ai_error = f"{exc} Showing the heuristic ranking instead."
    return {**found, "issues": merge_ranking(ranked, ai),
            "recommendation": (ai or {}).get("recommendation"),
            "ai_error": ai_error, "model": provider.model if provider and ai else None}


def api_plan(body: dict[str, Any]) -> dict[str, Any]:
    data = planner.plan_contribution(_text(body, "issue_url"))
    provider, ai_error = maybe_provider(bool(body.get("ai", True)))
    if provider:
        try:
            ai = llm.ask_json(provider, "plan", llm.validate_plan, repo=data["repo"],
                              number=data["issue"]["number"],
                              facts=json.dumps(plan_facts(data), ensure_ascii=False))
            data["markdown"] = apply_refinement(data, ai)
            data["model"] = provider.model
        except (ProviderError, llm.LLMError) as exc:
            ai_error = f"{exc} Showing the template plan instead."
    data["ai_error"] = ai_error
    return data


def api_index(body: dict[str, Any]) -> dict[str, Any]:
    """Build (or reuse) the chat's search index for a repo, so the first question is quick."""
    info = repo_docs.fetch_repo_docs(_text(body, "repo"), health=False)
    owner, repo = info["repo"].split("/")
    return rag.get_index(owner, repo, info["default_branch"]).stats()


def _history_text(history: Any, question: str) -> tuple[str, str]:
    """(earlier conversation as text, the user's previous question) from the page's messages."""
    turns = [m for m in history if isinstance(m, dict) and m.get("role") in ("User", "Assistant")]
    last = turns[-1] if turns else {}
    if last.get("role") == "User" and str(last.get("content", "")).strip() == question:
        turns = turns[:-1]  # the page includes the new question; the prompt has it separately
    turns = turns[-CHAT_HISTORY_TURNS:]
    text = "\n\n".join(f"{m['role']}: {str(m.get('content', ''))[:1500]}" for m in turns)
    asked = [str(m.get("content", "")) for m in turns if m["role"] == "User"]
    # The previous question gives a follow-up ("and where is that tested?") something to search for.
    return text or "(none yet)", asked[-1] if asked else ""


def _issue_facts(data: dict[str, Any]) -> dict[str, Any]:
    """The plan's issue facts plus the issue's discussion."""
    owner, repo = data["repo"].split("/")
    number = data["issue"]["number"]
    comments = github.api_get(f"/repos/{owner}/{repo}/issues/{number}/comments",
                              {"per_page": 30})
    return {**plan_facts(data), "comments": [
        {"author": (c.get("user") or {}).get("login", "?"), "body": (c.get("body") or "")[:1500]}
        for c in comments[-15:]
    ]}


def api_chat(body: dict[str, Any]) -> dict[str, Any]:
    """Answer a question about a repo (mode "repo") or one of its issues (mode "issue").

    Retrieval-augmented: the question is searched against the repo's files and the best
    matching chunks go into the prompt next to the repo or issue facts.
    """
    question = _text(body, "question")
    mode = body.get("mode", "repo")
    if mode not in ("repo", "issue"):
        raise ApiError("'mode' must be 'repo' or 'issue'.")
    provider, ai_error = maybe_provider(True)
    if not provider:
        raise ApiError(ai_error or "AI provider unavailable.")
    history, previous_question = _history_text(body.get("history", []), question)

    if mode == "issue":
        data = planner.plan_contribution(_text(body, "issue_url"))
        repo_name, branch = data["repo"], data["default_branch"]
        facts = _issue_facts(data)
        context = {"issue": json.dumps(facts, ensure_ascii=False),
                   "number": data["issue"]["number"]}
        similar = (data.get("similar_prs") or {}).get("examples", [])
        known = citations.Known.from_stack(
            data["stack"],
            issue_numbers={data["issue"]["number"]}
            | {n for ex in similar for n in (ex["issue"]["number"], ex["pr"]["number"])},
            comment_authors={c["author"] for c in facts["comments"]})
        query = f"{question} {previous_question}"
        # Issue titles are often vague ("UX improvement"); the description names the parts.
        search_context = f"{data['issue']['title']} {data['issue']['body'][:1500]}"
    else:
        info = repo_docs.fetch_repo_docs(_text(body, "repo"), health=False)
        repo_name, branch = info["repo"], info["default_branch"]
        context = {"facts": json.dumps(analyze_facts(info), ensure_ascii=False)}
        known = citations.Known.from_stack(info["stack"])
        query, search_context = f"{question} {previous_question}", ""

    # Without the index the chat still answers from the facts, and says why code is missing.
    chunks, index, index_error = [], None, None
    try:
        index = rag.get_index(*repo_name.split("/"), branch)
        chunks = index.search(query, context=search_context)
    except GitHubError as exc:
        index_error = str(exc)
    snippets = rag.format_snippets(chunks) if not index_error else f"(unavailable: {index_error})"

    try:
        reply = llm.ask(provider, "chat_issue" if mode == "issue" else "chat", repo=repo_name,
                        snippets=snippets, history=history, question=question, **context)
    except (ProviderError, llm.LLMError) as exc:
        raise ApiError(str(exc)) from exc
    checked = citations.verify(reply, index, known)
    return {"reply": reply, "repo": repo_name, "branch": branch, "index_error": index_error,
            "sources": [{"path": c.path, "start": c.start, "end": c.end} for c in chunks],
            "citations": checked, "citation_summary": citations.summary(checked)}


def api_tree(body: dict[str, Any]) -> dict[str, Any]:
    owner, repo = repo_docs.parse_repo(_text(body, "repo"))
    branch = _text(body, "branch")
    paths, truncated = repo_docs.fetch_tree(owner, repo, branch)
    return {"repo": f"{owner}/{repo}", "branch": branch, "paths": sorted(paths),
            "truncated": truncated}


ROUTES = {"/api/analyze": api_analyze, "/api/issues": api_issues, "/api/plan": api_plan,
          "/api/chat": api_chat, "/api/tree": api_tree, "/api/index": api_index}


# --- HTTP plumbing ----------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "contrib-buddy"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        pass  # keep the terminal quiet; errors are printed explicitly

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: HTTPStatus, data: Any) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            page = (STATIC_DIR / "index.html").read_bytes()
            self._send(HTTPStatus.OK, page, "text/html; charset=utf-8")
        elif path == "/api/status":
            self._json(HTTPStatus.OK, provider_status())
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            self.close_connection = True
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Request too large."})
            return
        raw = self.rfile.read(length)  # read before replying, or Windows resets the socket
        handler = ROUTES.get(self.path)
        if not handler:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            return
        # Requiring a JSON content type forces a CORS preflight, which this server never
        # answers, so other websites open in the browser cannot trigger requests here.
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "Send JSON."})
            return
        try:
            body = json.loads(raw or b"{}")
            if not isinstance(body, dict):
                raise ApiError("Send a JSON object.")
            self._json(HTTPStatus.OK, handler(body))
        except (ApiError, GitHubError) as exc:
            status = getattr(exc, "status", HTTPStatus.BAD_REQUEST)
            self._json(status, {"error": str(exc)})
        except ValueError:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON."})
        except Exception as exc:  # noqa: BLE001 - report anything else to the page
            traceback.print_exc()
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"Unexpected error: {exc}"})


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    """Run the web app until Ctrl+C."""
    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"contrib-buddy is running at {url}  (press Ctrl+C to stop)", file=sys.stderr)
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
