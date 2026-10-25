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
from buddy.skill import (
    GitHubError,
    discovery,
    git_helper,
    github,
    issues,
    matcher,
    planner,
    prechecker,
    ranker,
    repo_docs,
)
from buddy.skill import doctor as doctor_mod  # the `doctor` command's name is taken

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Open Source Contributor Buddy: from zero to your first PR, powered by open models.",
)
for _stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252; emoji would crash
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
console = Console()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
NO_AI_HELP ="Skip the model; show facts and heuristics only."
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
        "health": health_facts(info.get("health")),
        "readme": doc(docs["readme"], 3500), "contributing": doc(docs["contributing"], 7000),
        "pr_template": doc(docs["pr_templates"][0] if docs["pr_templates"] else None, 1500),
    }


def health_facts(health: dict[str, Any] | None) -> dict[str, Any] | None:
    """The health verdict and its reasons, without the raw numbers."""
    if not health or not health.get("verdict"):
        return None
    return {"verdict": health["label"], "summary": health["summary"],
            "signals": {s["label"]: s["detail"] for s in health["signals"]}}


HEALTH_STYLE = {"welcoming": "green", "slow": "yellow", "inactive": "red", "unknown": "dim"}
HEALTH_ICON = {"good": "✔", "ok": "!", "bad": "✘", "unknown": "–", "info": "i"}
SIGNAL_STYLE = {"good": "green", "ok": "yellow", "bad": "red", "unknown": "dim", "info": "cyan"}


def print_health(health: dict[str, Any] | None) -> None:
    """The Welcoming / Slow / Inactive badge and the signals behind it."""
    if not health:
        return
    if not health.get("verdict"):
        console.print(f"[dim]Repo health: could not check ({esc(health.get('error', ''))})[/]")
        return
    style = HEALTH_STYLE[health["verdict"]]
    lines = [f"[bold {style}]● {health['label']}[/]  {esc(health['summary'])}"]
    for s in health["signals"]:
        icon = HEALTH_ICON[s["status"]]
        lines.append(f"  [{SIGNAL_STYLE[s['status']]}]{icon}[/] {s['label']}: "
                     f"[dim]{esc(s['detail'])}[/]")
    console.print(Panel("\n".join(lines), title="Repo health", title_align="left",
                        border_style=style))


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
    print_health(info.get("health"))
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

def annotate_issues(found: dict[str, Any], ranked: list[dict[str, Any]],
                    skills: list[str] | None) -> list[dict[str, Any]]:
    """Add each issue's contribution type, the technologies it needs and, with skills, the fit."""
    try:  # cached from analyze; only a fallback when an issue names no technology
        langs = github.api_get(f"/repos/{found['repo']}/languages")
        repo_languages = sorted(langs, key=lambda k: -langs[k])
    except GitHubError:
        repo_languages = []
    return matcher.annotate(ranked, repo_languages, skills)


def issues_facts(ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compact issue data for the ranking prompt."""
    return [
        {"number": i["number"], "title": i["title"], "labels": i["labels"],
         "age_days": i["age_days"], "comments": i["comments"], "body": i["body"][:1200],
         "heuristic": i["heuristic"], "contribution": i.get("contribution", {}).get("type"),
         "needs": i.get("techs"), "skill_fit": (i.get("skill_match") or {}).get("fit")}
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
    skills: str = typer.Option("", "--skills", "-s",
                               help='What you know, e.g. "python, react", to match issues.'),
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
    known = matcher.normalize_skills(skills) if skills else None
    ranked = annotate_issues(found, ranker.rank_issues(found["issues"]), known)
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
                    ("Type", {}), ("Level", {}), ("Fit", {}),
                    ("Clarity", {"justify": "right"}), ("Age", {"justify": "right"}),
                    ("💬", {"justify": "right"})):
        table.add_column(col, **kw)
    level_style = {"easy": "green", "medium": "yellow", "hard": "red"}
    fit_style = {"good": "green", "partial": "yellow", "stretch": "red", "any": "dim"}
    for issue in final:
        level = issue.get("ai", {}).get("difficulty") or issue["heuristic"]["difficulty"]
        kind = issue["contribution"]
        kind_text = f"[green]{kind['label']}[/]" if kind["low_risk"] else kind["label"]
        match = issue.get("skill_match")
        fit = f"[{fit_style[match['fit']]}]{match['fit']}[/]" if match else \
            esc(", ".join(issue["techs"][:2]) or "–")
        table.add_row(f"[link={issue['url']}]{issue['number']}[/link]", esc(issue["title"]),
                      kind_text, f"[{level_style[level]}]{level}[/]", fit,
                      str(issue["heuristic"]["clarity"]), f"{issue['age_days']}d",
                      str(issue["comments"]))
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

def similar_facts(similar: dict[str, Any]) -> list[dict[str, Any]]:
    """The similar merged PRs, trimmed for the plan prompt."""
    return [{"issue": f"#{ex['issue']['number']} {ex['issue']['title']}",
             "pr": f"#{ex['pr']['number']} {ex['pr']['title']}",
             "size": f"+{ex['pr']['additions']}/-{ex['pr']['deletions']}, "
                     f"{ex['pr']['changed_files']} files",
             "files": [f["path"] for f in ex["pr"]["files"][:8]],
             "review": ex["pr"]["review_story"]}
            for ex in similar.get("examples", [])]


def plan_facts(data: dict[str, Any]) -> dict[str, Any]:
    """Compact plan data for the refinement prompt."""
    return {
        "issue": {**data["issue"], "body": data["issue"]["body"][:3000]},
        "likely_files": data["likely_files"],
        "similar_merged_prs": similar_facts(data.get("similar_prs") or {}),
        "claim_check": {k: (data.get("claim_check") or {}).get(k)
                        for k in ("label", "findings", "questions")},
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
    check = data.get("claim_check") or {}
    if check.get("questions") and ai.get("questions"):  # issue-specific wording of the same asks
        check["questions"] = [q.strip() for q in ai["questions"]]
        data["claim_comment"] = planner._claim_comment(check)
    data["claim_comment"] = data["claim_comment"].replace(
        planner.PLAN_PLACEHOLDER, ai["claim_one_line"].rstrip("."))
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
    plan_file: Path | None = typer.Option(None, "--plan",
                                          help="Plan JSON (buddy plan --json) to compare with."),
    issue: str | None = typer.Option(None, "--issue",
                                     help="Issue URL: build its plan and compare your diff."),
    no_ai: bool = typer.Option(False, "--no-ai", help=NO_AI_HELP),
    as_json: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Run the pre-PR checklist in the current git repo."""
    provider = provider_or_exit(no_ai)
    plan_data = None
    try:
        if plan_file:
            plan_data = json.loads(plan_file.read_text(encoding="utf-8"))
        elif issue:
            with console.status("Building the plan to compare your changes with…"):
                plan_data = planner.plan_contribution(issue)
        with console.status("Running pre-PR checks…"):
            result = prechecker.precheck(base, str(pr_body) if pr_body else None,
                                         not skip_commands, timeout, plan_data)
    except GitHubError as exc:
        fail(str(exc))
    except (OSError, ValueError) as exc:
        fail(f"Cannot read the plan: {exc}",
             "Create it with: buddy plan <issue-url> --json > plan.json")
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


@app.command()
def doctor(
    path: Path = typer.Option(Path("."), "--path", help="Your local clone (default: here)."),
    as_json: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Check your installed tools and versions against what this repo needs."""
    with console.status("Reading the repo's requirements and checking your tools…"):
        result = doctor_mod.doctor(str(path))
    if as_json:
        dump(result)
        raise typer.Exit(0 if result["ready"] else 1)
    style = {"pass": "green", "warn": "yellow", "fail": "red", "missing": "red", "unknown": "dim"}
    table = Table(title=f"Setup doctor: {result['path']}", title_justify="left",
                  header_style="bold")
    for col in ("", "Tool", "Needs", "You have", "From"):
        table.add_column(col)
    for c in result["checks"]:
        table.add_row(f"[{style[c['status']]}]{doctor_mod.ICON[c['status']]}[/]", c["tool"],
                      esc(c["required"] or "any"), esc(c["installed"] or "not found"),
                      esc(c["source"]))
    console.print(table)
    for c in result["checks"]:
        if c["fix"]:
            console.print(f"[{style[c['status']]}]{c['tool']}:[/] {esc(c['fix'])}")
    if not result["checks"]:
        console.print("[dim]No version requirements found here. Run it inside your clone.[/]")
    next_step("Install or update the ✘ items, then run the setup commands from the plan."
              if not result["ready"] else "You're set up. Install the project's dependencies "
              "and run its tests once before changing anything.")
    raise typer.Exit(0 if result["ready"] else 1)


@app.command(name="git")
def git_cmd(
    repo: str | None = typer.Option(None, "--repo", help="owner/repo, to print all recipes."),
    branch: str | None = typer.Option(None, "--branch", help="Your branch name."),
    default: str = typer.Option("main", "--default", help="The project's default branch."),
    path: Path = typer.Option(Path("."), "--path", help="Your local clone (default: here)."),
    as_json: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Check your clone's git state and show the exact commands to fix or sync it."""
    if repo:
        result: dict[str, Any] = {"recipes": git_helper.recipes(repo, default, branch),
                                  "findings": []}
    else:
        with console.status("Reading your clone's git state…"):
            result = git_helper.diagnose(str(path))
    if as_json:
        dump(result)
        return
    level_style = {"error": "red", "warn": "yellow", "info": "cyan", "ok": "green"}
    for f in result["findings"]:
        body = esc(f["message"]) + "".join(f"\n  $ {esc(c)}" for c in f["commands"])
        console.print(Panel(body, border_style=level_style[f["level"]], title=f["level"],
                            title_align="left"))
    wanted = {f["recipe"] for f in result["findings"] if f["recipe"]}
    for recipe in result.get("recipes") or []:
        if repo or recipe["id"] in wanted:
            lines = [f"[dim]{esc(recipe['when'])}[/]"]
            for s in recipe["steps"]:
                warn = f"\n    [red]⚠ {esc(s['danger'])}[/]" if s["danger"] else ""
                lines.append(f"[green]{esc(s['cmd'])}[/]\n    {esc(s['why'])}{warn}")
            console.print(Panel("\n".join(lines), title=recipe["title"], title_align="left"))
    if not repo:
        next_step("All recipes for this project:  [bold]buddy git --repo OWNER/REPO[/]")


@app.command()
def tour(
    repo: str = typer.Argument(..., help="owner/repo or GitHub URL"),
    no_ai: bool = typer.Option(False, "--no-ai", help=NO_AI_HELP),
    as_json: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """A guided tour of how the repo is organized and where things happen."""
    from buddy import tour as tour_mod

    provider = provider_or_exit(no_ai)
    try:
        with console.status("Indexing the code and writing the tour…"):
            result = tour_mod.run(repo, provider)
    except GitHubError as exc:
        fail(str(exc))
    if as_json:
        dump(result)
        return
    if result["ai_error"]:
        console.print(f"[yellow]{esc(result['ai_error'])}[/]")
    console.print(Markdown(result["markdown"]))
    flagged = [c["raw"] for c in result["citations"] if c["status"] in ("unsupported",
                                                                         "not_found")]
    if flagged:
        console.print(f"[yellow]⚠ Couldn't verify: {esc(', '.join(flagged))}[/]")
    next_step(f"Ask where something happens:  [bold]buddy web[/] → chat about {result['repo']}")


@app.command()
def discover(
    languages: str = typer.Option("", "--languages", "-l", help='e.g. "python, typescript"'),
    interests: str = typer.Option("", "--interests", "-i", help='e.g. "cli, data viz, games"'),
    limit: int = typer.Option(6, "--limit", "-n", min=1, max=10, help="Repos to suggest."),
    as_json: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Suggest welcoming repos to contribute to, from your languages and interests."""
    if not languages.strip() and not interests.strip():
        fail("Tell me what you know or like.", 'Example: buddy discover -l python -i "cli, games"')
    try:
        with console.status("Searching GitHub and checking how welcoming each repo is…"):
            found = discovery.discover_repos(languages, interests, limit)
    except GitHubError as exc:
        fail(str(exc))
    if as_json:
        dump(found)
        return
    if not found["repos"]:
        console.print("[yellow]No active repos with open beginner issues matched. Try broader "
                      "interests or another language.[/]")
        return
    for n, r in enumerate(found["repos"], 1):
        health = r["health"]
        style = HEALTH_STYLE.get(health.get("verdict"), "dim")
        matched = ", ".join(r["matched_languages"] + r["matched_interests"]) or "–"
        body = (f"{esc(r['description'] or '')}\n⭐ {r['stars']}  ·  {r['language'] or '?'}  ·  "
                f"matches: {esc(matched)}\n[{style}]● {health['label']}[/] "
                f"{esc(health.get('summary') or health.get('error') or '')}\n"
                f"Good first issues: {r['good_first_issues_url']}")
        console.print(Panel(body, title=f"{n}. [bold]{r['repo']}[/]", title_align="left",
                            border_style=style))
    top = found["repos"][0]["repo"]
    next_step(f"Pick one and see how to contribute:  [bold]buddy analyze {top}[/]")


@app.command(name="coach")
def coach_cmd(
    pr_url: str | None = typer.Argument(None, help="https://github.com/OWNER/REPO/pull/N"),
    text_file: Path | None = typer.Option(None, "--text", "-t",
                                          help="Pasted review comments or a CI log ('-' = stdin)."),
    repo: str | None = typer.Option(None, "--repo", help="owner/repo, when only --text is given."),
    no_ai: bool = typer.Option(False, "--no-ai", help=NO_AI_HELP),
    as_json: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Explain review comments or a failing CI check on your PR, and how to fix them."""
    from buddy import coach

    provider = provider_or_exit(no_ai)
    text = None
    if text_file:
        text = sys.stdin.read() if str(text_file) == "-" else \
            text_file.read_text(encoding="utf-8", errors="replace")
    if not pr_url and not text:
        fail("Give a pull request link, or --text with review comments or a CI log.",
             "Example: buddy coach --text ci.log --repo owner/repo")
    try:
        with console.status("Reading the reviews, checks and code…"):
            result = coach.run(pr_url, text, repo, provider)
    except GitHubError as exc:
        fail(str(exc))
    if as_json:
        dump(result)
        return

    pr = result["facts"].get("pr")
    if pr:
        console.print(Panel(f"[bold]{esc(pr['title'] or '')}[/]\n{pr['url']}  ·  "
                            f"{pr['head']} → {pr['base']}", title=f"PR #{pr['number']}",
                            title_align="left"))
    if result["facts"].get("logs_need_token"):
        console.print("[dim]Set GITHUB_TOKEN to read failed job logs (GitHub requires it).[/]")
    ai = result["ai"]
    if ai and provider:
        ai_header(provider)
        console.print(Markdown(ai["summary"]))
        for n, item in enumerate(ai["items"], 1):
            body = f"{item['what']}\n\n{item['fix']}"
            if item["commands"]:
                body += "\n\n```bash\n" + "\n".join(item["commands"]) + "\n```"
            flagged = sum(c["status"] in ("unsupported", "not_found")
                          for f in item["citations"].values() for c in f)
            title = f"{n}. {item['title']}" + (f"  (⚠ {flagged} unverified citation(s))"
                                               if flagged else "")
            console.print(Panel(Markdown(body), title=esc(title), title_align="left",
                                border_style="magenta" if item["kind"] == "review" else "cyan"))
        if ai.get("reply"):
            console.print(Panel(esc(ai["reply"]), title="Reply draft (post it yourself)",
                                title_align="left", border_style="green"))
    else:
        if result["ai_error"]:
            console.print(f"[yellow]{esc(result['ai_error'])}[/]")
        if not result["guidance"]:
            console.print("Nothing recognisable found. Paste the full log or review text.")
        for g in result["guidance"]:
            body = g["explanation"]
            if g["where"]:
                body += "\nWhere: " + ", ".join(g["where"])
            if g["commands"]:
                body += "\n\n" + "\n".join(f"  $ {c}" for c in g["commands"])
            console.print(Panel(esc(body), title=esc(g["title"]), title_align="left",
                                subtitle=esc(g["source"]), subtitle_align="right"))
    next_step("Make the changes, push to the same branch (the PR updates by itself), then "
              "reply on the PR yourself.")


@app.command()
def web(
    port: int = typer.Option(8765, "--port", "-p", help="Port to serve on."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open a browser tab."),
) -> None:
    """Open the contrib-buddy web app in your browser."""
    from buddy.web import serve

    try:
        serve(port=port, open_browser=not no_browser)
    except OSError as exc:
        fail(f"Could not start the web app on port {port}: {exc}",
             "Is it already running? Try another port with --port 8766.")


@app.callback()
def _main() -> None:
    load_dotenv()
    load_dotenv(PROJECT_ROOT / ".env")  # also works when started outside the project folder


if __name__ == "__main__":
    app()
