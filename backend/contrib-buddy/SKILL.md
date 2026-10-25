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

Start with `health`: its `label` (Welcoming / Slow / Inactive / Not enough data) and `summary` say whether maintainers respond to outside contributors, based on the last commit, median first response on recent PRs, outside contributors' merge rate, the open PR backlog and any stale bot (`signals`, each with a `detail`). If the verdict is Slow or Inactive, say so first and suggest the user ask on an issue before investing time. If `health.error` is set, say the health check could not run.

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
This outputs a Markdown plan (fork/clone commands, branch name, setup and test commands, similar merged PRs, likely files, commit message and PR description drafts using the repo's PR template). The "Learn from similar merged PRs" section lists closed issues like this one and the PRs that fixed them: files touched, size and how review went (from `similar_prs.py`, also runnable on its own). Likely files marked "changed by similar merged PR" are real history; the rest are keyword matches. Point the user to the closest example PR as a model for scope and style. The "Before you claim" section (from `claim_check.py`, also runnable on its own) says whether someone may already be on the issue: assigned, an open PR that mentions it or looks like the same work, or an "I'll take this" comment. It also lists questions for the maintainer when the issue is vague. If the verdict is "Someone may be on it" or "Ask first", tell the user that before anything else, and offer the generated comment (a check-in or questions) instead of a plain claim. Review it, improve the "Approach" wording for this specific issue, and present it. Offer the "claim this issue" comment from [assets/claim-comment-template.md](assets/claim-comment-template.md) for the user to post **themselves** if the project expects claiming. See [references/good-pr-etiquette.md](references/good-pr-etiquette.md).

### 4. Before opening the PR
Run inside the user's local clone:
```
python /path/to/contrib-buddy/scripts/precheck_pr.py --base main [--pr-body pr.md]
```
Walk the user through each `fail`/`warn` item and how to fix it. Use `--skip-commands` if running lint/tests is too slow or unsafe.

### 5. After opening the PR: review comments and failing checks
```
python scripts/pr_coach.py https://github.com/OWNER/REPO/pull/45 [--text review-or-ci-log.txt]
```
It prints open review `threads` (file, line, diff, replies; `waiting_on_you` means the reviewer spoke last), each reviewer's latest verdict, failed `checks` with annotations and parsed log errors (logs need `GITHUB_TOKEN`), parsed pasted text, and `guidance` with a concrete fix for each kind of failure. Give one item per request, most blocking first. Explain jargon (nit, LGTM, rebase), say whether each item blocks the merge, and give the fix using the `guidance` commands. Flag failures that look flaky or unrelated to the PR so the user can ask a maintainer to re-run them. Draft a short, polite reply, which the user posts **themselves** after pushing. Never push, re-run checks or comment for them. See [references/good-pr-etiquette.md](references/good-pr-etiquette.md).

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
