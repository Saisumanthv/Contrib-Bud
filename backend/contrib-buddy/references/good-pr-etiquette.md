# Good PR etiquette

## Claiming an issue
- Read the whole thread first. If someone asked to work on it recently (roughly the last 2 weeks), pick another issue.
- Check CONTRIBUTING.md: some projects say "don't ask to be assigned, just open a PR"; others require assignment first. Follow it.
- When claiming, be short and specific: say what you plan to do and roughly when. See `assets/claim-comment-template.md`.
- Don't claim several issues at once. Finish one first.
- If you can't continue, say so in the thread so someone else can pick it up.

## Before opening the PR
- Lint and tests pass locally (`scripts/precheck_pr.py`).
- The diff only contains changes for this issue (no stray formatting, lockfile churn, or debug prints).
- The PR template is fully filled in; checkboxes are honestly ticked.
- The issue is linked (`Fixes #123`).

## Writing the PR description
- **What** changed and **why** (link the issue).
- **How it was tested** — commands run, screenshots for UI.
- Anything reviewers should look at closely, or questions you have.

## Responding to review
- Thank reviewers; assume good intent. Reviews are about the code, not you.
- Address every comment: either change the code or explain why not, politely.
- Reply "Done" (or resolve the thread if the project lets authors resolve) after pushing a fix.
- If you disagree, ask a question rather than argue: "Would X work instead? I chose Y because…"
- Don't re-request review after every small push; batch your changes.

## Pinging and waiting
- Maintainers are often volunteers. Wait at least a week before a polite ping: "Friendly ping — happy to make changes if needed."
- Never DM maintainers privately about your PR unless invited.
- Don't open duplicate PRs or tag many people.

## Using AI responsibly
- You are responsible for every line you submit. Run it, test it, understand it.
- Follow the project's AI policy if it has one (check CONTRIBUTING.md).
- Never paste AI output into review replies without reading and editing it.
