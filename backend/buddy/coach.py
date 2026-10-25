"""Post-PR coach: explain review comments and failing checks, and suggest fixes.

Facts come from the skill's pr_coach.py (the PR's feedback and/or pasted text); this
adds the code being discussed, asks the model to explain it, and checks the model's
citations. Advice only: nothing is posted, pushed or re-run.
"""

from __future__ import annotations

import json
from typing import Any

from buddy import citations, llm, rag
from buddy.providers import Provider, ProviderError
from buddy.skill import GitHubError, coach, github, repo_docs

WINDOW = 12            # lines of code shown around each discussed line
MAX_LOCATIONS = 10
SEARCH_CHUNKS = 3      # extra chunks found by searching the error / review text


def locations(facts: dict[str, Any]) -> list[tuple[str, int | None]]:
    """(path, line) of everything reviewers and checks point at, most important first."""
    found: list[tuple[str, int | None]] = []
    for t in facts.get("threads", []):
        if not t["outdated"]:
            found.append((t["path"], t["line"]))
    logs = [(facts.get("pasted") or {}).get("log") or {}] + [
        c.get("log") or {} for c in facts.get("checks", [])]
    for log in logs:
        found += [(e["path"], int(e["line"]) if e.get("line") else None)
                  for e in log.get("errors", []) if e.get("path")]
        found += [(f["path"], int(f["line"])) for f in log.get("frames", [])]
    for check in facts.get("checks", []):
        found += [(a["path"], a.get("line")) for a in check.get("annotations", []) if a.get("path")]
    found += [(r["path"], r["line"]) for r in
              ((facts.get("pasted") or {}).get("review") or {}).get("file_refs", [])]
    return list(dict.fromkeys(found))[:MAX_LOCATIONS]


def resolve(index: rag.RepoIndex, raw: str) -> str | None:
    """A repo path for a path from a log, which may be absolute on the CI machine
    (/home/runner/work/app/app/src/x.py): the longest suffix that names one file."""
    parts = raw.replace("\\", "/").strip("/").split("/")
    for i in range(len(parts)):
        if path := index.resolve("/".join(parts[i:])):
            return path
    return None


def code_context(index: rag.RepoIndex, facts: dict[str, Any]) -> list[rag.Chunk]:
    """The lines around each location, then chunks matching the feedback's words."""
    chunks: list[rag.Chunk] = []
    for raw, line in locations(facts):
        path = resolve(index, raw)
        if not path:
            continue
        lines = index.lines[path]
        start = max(1, (line or 1) - WINDOW) if line else 1
        end = min(len(lines), (line or 0) + WINDOW) if line else min(len(lines), 2 * WINDOW)
        if any(c.path == path and c.start <= (line or 1) <= c.end for c in chunks):
            continue
        chunks.append(rag.Chunk(path, start, end, "\n".join(lines[start - 1:end])))
    words = " ".join([(facts.get("pasted") or {}).get("text", "")[:2000]] +
                     [c["body"] for t in facts.get("threads", [])[:6] for c in t["comments"]])
    if words.strip():
        taken = {(c.path, c.start) for c in chunks}
        chunks += [c for c in index.search(words, k=SEARCH_CHUNKS)
                   if (c.path, c.start) not in taken]
    return chunks


def model_facts(facts: dict[str, Any], guidance: list[dict], info: dict | None) -> dict:
    """The facts, trimmed to what the coach prompt needs."""
    def trim_log(log: dict | None, chars: int) -> dict | None:
        if not log:
            return None
        return {"errors": [{k: e[k] for k in ("kind", "tool", "text", "path", "line", "test")
                            if k in e} for e in log.get("errors", [])[:12]],
                "excerpt": log.get("excerpt", "")[:chars]}

    pasted = facts.get("pasted")
    stack = (info or {}).get("stack") or {}
    return {
        "pr": facts.get("pr"),
        "threads": [{**t, "comments": [{**c, "body": c["body"][:800]} for c in t["comments"]]}
                    for t in facts.get("threads", [])[:12]],
        "reviews": facts.get("reviews", []),
        "conversation": facts.get("conversation", [])[-8:],
        "checks": [{"name": c["name"], "kinds": c.get("kinds"), "summary": c["summary"][:600],
                    "annotations": c["annotations"][:8], "log": trim_log(c.get("log"), 3000)}
                   for c in facts.get("checks", [])],
        "pasted": pasted and {"kind": pasted["kind"],
                              "text": pasted["text"][:6000] if pasted["kind"] == "review" else None,
                              "log": trim_log(pasted["log"], 5000),
                              "review": pasted["review"]},
        "guidance": guidance,
        "test_commands": stack.get("test_commands", [])[:3],
        "lint_commands": stack.get("lint_commands", [])[:3],
        "rules": sorted(((info or {}).get("rules") or {}).keys()),
    }


def _checked(item: dict[str, Any], index: rag.RepoIndex | None,
             known: citations.Known) -> dict[str, Any]:
    """An item with its citations checked, separately for each Markdown field."""
    return {**item, "commands": item.get("commands", []),
            "citations": {f: citations.verify(item[f], index, known) for f in ("what", "fix")}}


def run(pr_url: str | None, text: str | None, repo: str | None,
        provider: Provider | None) -> dict[str, Any]:
    """Facts, rule-based fixes and (with a provider) the model's explanation."""
    facts = coach.coach_facts(pr_url, text)
    repo_name = facts.get("repo") or (
        "/".join(github.parse_repo(repo)) if repo else None)
    info = repo_docs.fetch_repo_docs(repo_name, health=False) if repo_name else None
    if info:
        repo_name = info["repo"]
    guidance = coach.guidance(facts, (info or {}).get("stack"))

    # Reviews and CI talk about the PR's own code: index the PR head when there is one.
    pr = facts.get("pr") or {}
    code_repo, ref = (pr["head_repo"], pr["head_sha"]) if pr.get("head_repo") else (
        repo_name, (info or {}).get("default_branch"))
    index, index_error, chunks = None, None, []
    if code_repo and ref:
        try:
            index = rag.get_index(*code_repo.split("/"), ref)
            chunks = code_context(index, facts)
        except GitHubError as exc:
            index_error = str(exc)

    result: dict[str, Any] = {
        "repo": repo_name, "code_repo": code_repo, "ref": ref, "facts": facts,
        "guidance": guidance, "index_error": index_error,
        "sources": [{"path": c.path, "start": c.start, "end": c.end} for c in chunks],
        "ai": None, "ai_error": None, "model": None}
    if not provider:
        return result
    try:
        ai = llm.ask_json(provider, "coach", llm.validate_coach, repo=repo_name or "the repository",
                          snippets=rag.format_snippets(chunks) if chunks else "(no code available)",
                          facts=json.dumps(model_facts(facts, guidance, info), ensure_ascii=False))
    except (ProviderError, llm.LLMError) as exc:
        result["ai_error"] = f"{exc} Showing the rule-based fixes instead."
        return result
    known = citations.Known.from_stack(
        (info or {}).get("stack") or {},
        issue_numbers={pr["number"]} if pr else None,
        comment_authors={c["author"] for t in facts.get("threads", []) for c in t["comments"]}
        | {r["reviewer"] for r in facts.get("reviews", [])})
    ai["items"] = [_checked(item, index, known) for item in ai["items"]]
    result.update(ai=ai, model=provider.model)
    return result
