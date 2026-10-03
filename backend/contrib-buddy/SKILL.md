---
name: contrib-buddy
description: Guides a new contributor from zero to their first pull request on any public GitHub repository. Reads the repo's README, CONTRIBUTING and templates, detects the stack and test commands, finds unclaimed beginner-friendly issues (good first issue, help wanted, hacktoberfest), ranks them, writes a step-by-step contribution plan with branch name, commit message and PR description drafts, and runs a pre-PR checklist. Use when the user says things like "help me contribute to a repo", "find a good first issue", "how do I contribute to owner/repo", "plan my fix for this issue", "prepare my PR", "is my PR ready", or mentions Hacktoberfest or open-source contributions.
license: MIT
compatibility: Requires Python 3.11+, the requests package, git, and internet access to api.github.com and raw.githubusercontent.com. Optional GITHUB_TOKEN env var raises the API rate limit.
metadata:
  author: contrib-buddy contributors
  version: "0.1.0"
---

# contrib-buddy

Help a human make a good first contribution. **You advise and draft; the human acts.**

## Ground rules

- **Facts come from scripts, not memory.** Every claim about a repo (rules, commands, issues, files) must come from script output. Cite the source inline, e.g. `(CONTRIBUTING.md)`, `(#1234)`, `(package.json)`.
- If a script says something is unknown, say "unknown — check the repo" rather than guessing.
- **Never** open PRs, post comments, push, or assign issues on the user's behalf. Give them the exact text and commands to run themselves.
- Scripts print JSON to stdout (`plan_contribution.py` prints Markdown unless `--json`; `precheck_pr.py --markdown` prints a checklist) and friendly errors to stderr with a non-zero exit code. If a script reports a rate limit, tell the user to set `GITHUB_TOKEN` or wait.

Run scripts from the skill root with `python scripts/<name>.py`. All take `--help`.

## Workflow

### 1. Understand the repo
```
python scripts/fetch_repo_docs.py OWNER/REPO
```
Summarize for the user in plain English: what the project is, how to set it up, how to run tests (`stack.test_commands`), and the contribution rules that matter (`rules`: DCO sign-off, CLA, issue-link requirement, commit convention). Flag anything that blocks a first-timer (e.g., CLA must be signed first).

### 2. Find an issue
```
python scripts/find_issues.py OWNER/REPO --limit 10 > issues.json
python scripts/rank_issues.py issues.json
```
`find_issues.py` already removes assigned issues, issues with a linked PR, and issues with a recent "I'll take this" comment. `rank_issues.py` adds a heuristic `clarity` score (0–100) and `difficulty` (easy/medium/hard) with `reasons`. Refine that ranking with your own reading of each issue, explain the top 3 in one or two sentences each, and recommend one. Do not invent issues that are not in the JSON.

### 3. Plan the contribution
```
python scripts/plan_contribution.py https://github.com/OWNER/REPO/issues/123
```
This outputs a Markdown plan (fork/clone commands, branch name, setup and test commands, likely files, commit message and PR description drafts using the repo's PR template). Review it, improve the "Approach" wording for this specific issue, and present it. Offer the "claim this issue" comment from [assets/claim-comment-template.md](assets/claim-comment-template.md) for the user to post **themselves** if the project expects claiming. See [references/good-pr-etiquette.md](references/good-pr-etiquette.md).

### 4. Before opening the PR
Run inside the user's local clone:
```
python /path/to/contrib-buddy/scripts/precheck_pr.py --base main [--pr-body pr.md]
```
Walk the user through each `fail`/`warn` item and how to fix it. Use `--skip-commands` if running lint/tests is too slow or unsafe.

## References (load only when needed)

- [references/contribution-workflow.md](references/contribution-workflow.md) — fork → branch → commit → PR flow, syncing with upstream, fixing review feedback.
- [references/good-pr-etiquette.md](references/good-pr-etiquette.md) — claiming issues politely, responding to review, when to ping.
- [references/hacktoberfest-rules.md](references/hacktoberfest-rules.md) — valid vs. spammy PRs during Hacktoberfest.
- [assets/](assets/) — templates for the plan, PR description, and claim comment.

## Edge cases

- **No beginner labels / no open issues:** suggest docs fixes, reproducing open bugs, or improving tests, and tell the user to ask maintainers first.
- **Repo archived or not found:** the script exits with a clear message; relay it and stop.
- **Issue already claimed after all:** suggest the next-ranked issue instead of competing.
- **Hacktoberfest:** since 2026, PRs no longer count toward Hacktoberfest rewards. Never encourage low-effort PRs to "count" for anything; see [references/hacktoberfest-rules.md](references/hacktoberfest-rules.md).
