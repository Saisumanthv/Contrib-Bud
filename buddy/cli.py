"""`buddy` — the contrib-buddy command-line runner.

Scripts gather facts from GitHub; an open-weight model (Gemma 4 or any Ollama
model) does the language work: explaining, ranking and drafting.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape as esc
from rich.panel import Panel
from rich.table import Table

from buddy import llm
from buddy.providers import Provider, ProviderError, get_provider
from buddy.skill import GitHubError, issues, planner, prechecker, ranker, repo_docs

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Open Source Contributor Buddy: from zero to your first PR, powered by open models.",
)
for _stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252; emoji would crash
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
console = Console()
NO_AI_HELP = "Skip the model; show facts and heuristics only."
STATUS_STYLE = {"pass": "green", "warn": "yellow", "fail": "red", "skip": "dim"}
STATUS_ICON = {"pass": "✔", "warn": "!", "fail": "✘", "skip": "–"}


# --- helpers ------------------------------------------------------------------

def load_dotenv(path: Path = Path(".env")) -> None:
    """Load KEY=VALUE lines from .env into os.environ (without overriding)."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            value = value.strip().strip("'\"")
            if value:
                os.environ.setdefault(key.strip(), value)


def fail(message: str, hint: str = "") -> None:
    """Print a friendly error and exit 1."""
    console.print(f"[bold red]Error:[/] {esc(message)}")
    if hint:
        console.print(f"[dim]{esc(hint)}[/]")
    raise typer.Exit(1)


def provider_or_exit(no_ai: bool) -> Provider | None:
    """Build the configured model provider (fail fast on bad config)."""
    if no_ai:
        return None
    try:
        return get_provider()
    except ProviderError as exc:
        fail(str(exc))
    return None


def next_step(text: str) -> None:
    """Print the 'next step' footer every command ends with."""
    console.print(Panel(text, title="Next step", title_align="left", border_style="cyan"))


def ai_header(provider: Provider) -> None:
    console.rule(f"[bold magenta]AI summary[/] [dim]({provider.name}: {provider.model})[/]")


def dump(data: Any) -> None:
    """Print JSON for --json mode."""
    console.print_json(json.dumps(data, ensure_ascii=False, default=str))


def _cmd_table(title: str, items: list[dict[str, str]]) -> Table:
    table = Table(title=title, title_justify="left", show_edge=False, header_style="bold")
    table.add_column("Command", style="green")
    table.add_column("Source", style="dim")
    for item in items:
        table.add_row(item["command"], item["source"])
    return table


# --- analyze --------------------------------------------------------------------

def analyze_facts(info: dict[str, Any]) -> dict[str, Any]:
    """Trim fetch_repo_docs output to what the model needs."""
    docs = info["docs"]

    def doc(d: dict | None, limit: int) -> dict | None:
        return {"path": d["path"], "content": d["content"][:limit]} if d else None

    return {
        "repo": info["repo"], "description": info["description"], "topics": info["topics"],
        "license": info["license"], "default_branch": info["default_branch"],
        "stack": info["stack"], "rules": info["rules"], "warnings": info["warnings"],
        "readme": doc(docs["readme"], 3500), "contributing": doc(docs["contributing"], 7000),
        "pr_template": doc(docs["pr_templates"][0] if docs["pr_templates"] else None, 1500),
    }


@app.command()
def analyze(
    repo: str = typer.Argument(..., help="owner/repo or GitHub URL, e.g. facebook/react"),
    no_ai: bool = typer.Option(False, "--no-ai", help=NO_AI_HELP),
    as_json: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Explain how to contribute to a repo: setup, tests and rules, in plain English."""
    provider = provider_or_exit(no_ai)
    try:
        with console.status(f"Reading {repo} docs from GitHub…"):
            info = repo_docs.fetch_repo_docs(repo)
    except GitHubError as exc:
        fail(str(exc))
    summary = None
    if provider:
        try:
            with console.status(f"Asking {provider.model} to explain…"):
                summary = llm.ask(provider, "analyze", repo=info["repo"],
                                  facts=json.dumps(analyze_facts(info), ensure_ascii=False))
        except ProviderError as exc:
            fail(str(exc), "Tip: add --no-ai to see the facts without a model.")
    if as_json:
        dump({"facts": info, "ai_summary": summary})
        return

    stack = info["stack"]
    langs = ", ".join(f"{lang['name']} {lang['percent']}%" for lang in stack["languages"][:4])
    console.print(Panel(
        f"[bold]{esc(info['description'] or '')}[/]\n"
        f"⭐ {info['stars']}  ·  license {info['license'] or 'none'}  ·  "
        f"default branch [cyan]{info['default_branch']}[/]\n{langs}",
        title=f"[bold]{info['repo']}[/]", title_align="left",
    ))
    found = [(k, v["path"]) for k, v in info["docs"].items() if isinstance(v, dict)]
    found += [("pr_template", t["path"]) for t in info["docs"]["pr_templates"]]
    console.print("[bold]Docs found:[/] " + (", ".join(f"{p}" for _, p in found) or "none"))
    for title, key in (("Install", "install_commands"), ("Test", "test_commands"),
                       ("Lint", "lint_commands"), ("CI runs", "ci_commands")):
        if stack[key]:
            console.print(_cmd_table(title, stack[key][:6]))
    if info["rules"]:
        rules = Table(title="Contribution rules detected", title_justify="left", show_edge=False,
                      header_style="bold")
        rules.add_column("Rule")
        rules.add_column("Evidence", style="dim", overflow="fold")
        for name, rule in info["rules"].items():
            rules.add_row(name, esc(f"{rule['source']}: {str(rule['evidence'])[:90]}"))
        console.print(rules)
    for warning in info["warnings"]:
        console.print(f"[yellow]⚠ {esc(warning)}[/]")
    if summary and provider:
        ai_header(provider)
        console.print(Markdown(summary))
    next_step(f"Find a beginner-friendly issue:  [bold]buddy issues {info['repo']}[/]")


# --- issues ---------------------------------------------------------------------

def issues_facts(ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compact issue data for the ranking prompt."""
    return [
        {"number": i["number"], "title": i["title"], "labels": i["labels"],
         "age_days": i["age_days"], "comments": i["comments"], "body": i["body"][:1200],
         "heuristic": i["heuristic"]}
        for i in ranked
    ]


def merge_ranking(ranked: list[dict[str, Any]], ai: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Reorder heuristic-ranked issues by the model's ranking and attach its notes."""
    if not ai:
        return ranked
    by_num = {i["number"]: i for i in ranked}
    out = []
    for item in ai["ranked"]:
        issue = by_num.pop(item["number"], None)
        if issue:
            out.append({**issue, "ai": item})
    return out + list(by_num.values())  # anything the model skipped keeps heuristic order


@app.command(name="issues")
def issues_cmd(
    repo: str = typer.Argument(..., help="owner/repo or GitHub URL"),
    limit: int = typer.Option(8, "--limit", "-n", min=1, max=30, help="How many issues to show."),
    no_comment_check: bool = typer.Option(False, "--no-comment-check",
                                          help="Skip per-issue claim checks (fewer API calls)."),
    no_ai: bool = typer.Option(False, "--no-ai", help=NO_AI_HELP),
    as_json: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Find unclaimed beginner-friendly issues, ranked and explained."""
    provider = provider_or_exit(no_ai)
    try:
        with console.status(f"Searching {repo} for available beginner issues…"):
            found = issues.find_issues(repo, limit, 14, not no_comment_check)
    except GitHubError as exc:
        fail(str(exc))
    ranked = ranker.rank_issues(found["issues"])
    ai, ai_error = None, None
    if provider and ranked:
        try:
            with console.status(f"Asking {provider.model} to rank and explain…"):
                ai = llm.ask_json(
                    provider, "issues", llm.validate_ranking({i["number"] for i in ranked}),
                    repo=found["repo"], facts=json.dumps(issues_facts(ranked), ensure_ascii=False),
                )
        except ProviderError as exc:
            fail(str(exc), "Tip: add --no-ai to see the heuristic ranking without a model.")
        except llm.LLMError as exc:
            ai_error = str(exc)
    final = merge_ranking(ranked, ai)
    if as_json:
        dump({**found, "issues": final, "ai_recommendation": (ai or {}).get("recommendation")})
        return

    if not final:
        console.print(f"[yellow]No available beginner issues found in {found['repo']}.[/]")
        for s in found["skipped"][:5]:
            console.print(f"  [dim]skipped #{s['number']}: {esc(s['reason'])}[/]")
        next_step("Try another repo, or look for docs/test improvements and ask maintainers "
                  "first. Searched labels: " + ", ".join(found["labels_used"]))
        return

    table = Table(title=f"Available issues in {found['repo']}", title_justify="left",
                  header_style="bold", show_lines=False)
    for col, kw in (("#", {"justify": "right"}), ("Title", {"overflow": "fold", "ratio": 3}),
                    ("Level", {}), ("Clarity", {"justify": "right"}), ("Age", {"justify": "right"}),
                    ("💬", {"justify": "right"})):
        table.add_column(col, **kw)
    level_style = {"easy": "green", "medium": "yellow", "hard": "red"}
    for issue in final:
        level = issue.get("ai", {}).get("difficulty") or issue["heuristic"]["difficulty"]
        table.add_row(f"[link={issue['url']}]{issue['number']}[/link]", esc(issue["title"]),
                      f"[{level_style[level]}]{level}[/]", str(issue["heuristic"]["clarity"]),
                      f"{issue['age_days']}d", str(issue["comments"]))
    console.print(table)
    if found["note"]:
        console.print(f"[dim]{found['note']}[/]")
    if found["skipped"]:
        console.print(f"[dim]Filtered out {len(found['skipped'])} issue(s) that were assigned, "
                      "linked to a PR, or recently claimed.[/]")

    if ai and provider:
        ai_header(provider)
        for issue in final[:3]:
            console.print(f"[bold]#{issue['number']}[/] {esc(issue['title'])}\n  "
                          f"{esc(issue['ai']['explanation'])}\n  [dim]{esc(issue['ai']['why'])}[/]")
        rec = ai["recommendation"]
        console.print(Panel(esc(rec["reason"]), title=f"Recommended: #{rec['number']}",
                            title_align="left", border_style="green"))
        pick = next(i for i in final if i["number"] == rec["number"])
    else:
        if ai_error:
            console.print(f"[yellow]The model's ranking was unusable ({esc(ai_error)}); "
                          "showing the heuristic ranking instead.[/]")
        top = final[0]
        console.print(f"[bold]Top pick by heuristics:[/] #{top['number']} — "
                      + esc("; ".join(top["heuristic"]["reasons"][:4])))
        pick = top
    next_step(f"Get a step-by-step plan:  [bold]buddy plan {pick['url']}[/]")


# --- plan -----------------------------------------------------------------------

def plan_facts(data: dict[str, Any]) -> dict[str, Any]:
    """Compact plan data for the refinement prompt."""
    return {
        "issue": {**data["issue"], "body": data["issue"]["body"][:3000]},
        "likely_files": data["likely_files"],
        "test_commands": data["stack"]["test_commands"],
        "lint_commands": data["stack"]["lint_commands"],
        "rules": data["rules"],
        "recent_commit_subjects": data["commit_style"]["examples"],
    }


def apply_refinement(data: dict[str, Any], ai: dict[str, Any]) -> str:
    """Merge the model's drafts into the plan and re-render the Markdown."""
    desc = data["pr_description"]
    placeholder = "<!-- Describe your change in one or two sentences. -->"
    if placeholder in desc:
        desc = desc.replace(placeholder, ai["pr_summary"])
    else:
        first, _, rest = desc.partition("\n")
        desc = f"{first}\n\n{ai['pr_summary']}\n{rest}"
    data["pr_description"] = desc
    data["claim_comment"] = data["claim_comment"].replace(
        "<one line: what you will change>", ai["claim_one_line"].rstrip("."))
    return planner.render_markdown(data, approach=ai["approach"])


@app.command()
def plan(
    issue_url: str = typer.Argument(..., help="https://github.com/OWNER/REPO/issues/N"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Also save the plan here."),
    no_ai: bool = typer.Option(False, "--no-ai", help=NO_AI_HELP),
    as_json: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Write a copy-pasteable contribution plan for one issue."""
    provider = provider_or_exit(no_ai)
    try:
        with console.status("Collecting the issue, repo rules and file tree…"):
            data = planner.plan_contribution(issue_url)
    except GitHubError as exc:
        fail(str(exc))
    markdown = data["markdown"]
    ai_note = ""
    if provider:
        try:
            with console.status(f"Asking {provider.model} to draft the approach and PR text…"):
                ai = llm.ask_json(provider, "plan", llm.validate_plan, repo=data["repo"],
                                  number=data["issue"]["number"],
                                  facts=json.dumps(plan_facts(data), ensure_ascii=False))
            markdown = apply_refinement(data, ai)
            ai_note = f"Approach and PR summary drafted by {provider.model}; check them."
        except ProviderError as exc:
            fail(str(exc), "Tip: add --no-ai to get the template plan without a model.")
        except llm.LLMError as exc:
            ai_note = f"Model output unusable ({exc}); showing the template plan."
    data["markdown"] = markdown
    if output:
        output.write_text(markdown, encoding="utf-8")
    if as_json:
        dump(data)
        return
    console.print(Markdown(markdown))
    if ai_note:
        console.print(f"[dim]{esc(ai_note)}[/]")
    saved = f"Saved to {output}. " if output else "Tip: save it with -o plan.md. "
    next_step(f"{saved}Fork the repo and run section 1. When your change is committed, "
              "run [bold]buddy check[/] inside your clone.")


# --- check ----------------------------------------------------------------------

@app.command()
def check(
    base: str | None = typer.Option(None, "--base", help="Base ref, e.g. upstream/main."),
    pr_body: Path | None = typer.Option(None, "--pr-body", help="Your PR description draft."),
    skip_commands: bool = typer.Option(False, "--skip-commands",
                                       help="Don't run lint/test commands."),
    timeout: int = typer.Option(600, "--timeout", help="Per-command timeout (seconds)."),
    no_ai: bool = typer.Option(False, "--no-ai", help=NO_AI_HELP),
    as_json: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Run the pre-PR checklist in the current git repo."""
    provider = provider_or_exit(no_ai)
    try:
        with console.status("Running pre-PR checks…"):
            result = prechecker.precheck(base, str(pr_body) if pr_body else None,
                                         not skip_commands, timeout)
    except GitHubError as exc:
        fail(str(exc))
    advice = None
    problems = [c for c in result["checks"] if c["status"] in ("warn", "fail")]
    if provider and problems:
        try:
            with console.status(f"Asking {provider.model} how to fix the issues…"):
                advice = llm.ask(provider, "check", branch=result["branch"],
                                 facts=json.dumps(result["checks"], ensure_ascii=False))
        except ProviderError as exc:
            console.print(f"[yellow]Model unavailable: {esc(str(exc))}[/]")
    if as_json:
        dump({**result, "ai_advice": advice})
        raise typer.Exit(0 if result["ready"] else 1)

    table = Table(title=f"Pre-PR checklist: {result['branch']} vs {result['base']}",
                  title_justify="left", header_style="bold")
    table.add_column("")
    table.add_column("Check")
    table.add_column("Detail", overflow="fold")
    for c in result["checks"]:
        style = STATUS_STYLE[c["status"]]
        table.add_row(f"[{style}]{STATUS_ICON[c['status']]}[/]", esc(c["name"]), esc(c["detail"]))
    console.print(table)
    if advice and provider:
        ai_header(provider)
        console.print(Markdown(advice))
    if result["ready"]:
        next_step("Push your branch (git push -u origin HEAD) and open the PR on GitHub "
                  "yourself, pasting your PR description.")
    else:
        next_step("Fix the ✘ items above, commit, and run [bold]buddy check[/] again.")
    raise typer.Exit(0 if result["ready"] else 1)


@app.callback()
def _main() -> None:
    load_dotenv()


if __name__ == "__main__":
    app()
