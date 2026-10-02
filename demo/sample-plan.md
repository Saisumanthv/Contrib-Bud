# Contribution plan: Starting task after milestone doesn't work with >1 milestone

**Issue:** [mermaid-js/mermaid#4121](https://github.com/mermaid-js/mermaid/issues/4121) · **Labels:** Type: Bug / Error, Good first issue!, Status: Triage, fosshack
**Branch:** `fix/4121-starting-task-after-milestone-doesnt-wor` · **Base:** `develop`

## 1. Fork, clone, branch
Fork the repo on GitHub first (Fork button on https://github.com/mermaid-js/mermaid), then:
```bash
git clone https://github.com/YOUR-USER/mermaid.git
cd mermaid
git remote add upstream https://github.com/mermaid-js/mermaid.git
git fetch upstream
git checkout -b fix/4121-starting-task-after-milestone-doesnt-wor upstream/develop
```

## 2. Set up and run the tests (before changing anything)
```bash
pnpm install   # from pnpm-lock.yaml
pnpm test   # from package.json
```

Lint before committing:
```bash
pnpm lint   # from package.json
```

## 3. Contribution rules for this repo
- Changelog / changeset entry may be required — _.github/pull_request_template.md: "- [ ] :butterfly: If your PR makes a change that should be noted in one or more packages' changelogs, generate a changes"_

## 4. Likely files to look at
- `packages/mermaid/src/diagrams/gantt/ganttDb.js` (matches: gantt)
- `packages/mermaid/src/diagrams/gantt/ganttDiagram.ts` (matches: gantt)
- `packages/mermaid/src/diagrams/gantt/ganttRenderer.js` (matches: gantt)
- `packages/mermaid/src/diagrams/gantt/ganttDetector.ts` (matches: gantt)
- `packages/mermaid/src/diagrams/gantt/parser/gantt.jison` (matches: gantt)

## 5. Approach
1. Reproduce the problem (or confirm the missing behavior) locally.
2. Read the likely files above and find where the change belongs.
3. Write or update a test that fails before your change.
4. Make the smallest change that makes the test pass.
5. Run the full test suite and linters, then review your diff with `git diff`.

## 6. Commit
```bash
git add -p
git commit -m "Starting task after milestone doesn't work with >1 milestone (#4121)"
git push -u origin fix/4121-starting-task-after-milestone-doesnt-wor
```

## 7. PR description draft
Open a PR from `fix/4121-starting-task-after-milestone-doesnt-wor` against `mermaid-js/mermaid:develop` and paste:

````markdown
Fixes #4121

## :bookmark_tabs: Summary

Brief description about the content of your PR.

Resolves #<your issue id here>

## :straight_ruler: Design Decisions

Describe the way your implementation works or what design decisions you made if applicable.

### :clipboard: Tasks

Make sure you

- [ ] :book: have read the [contribution guidelines](https://mermaid.js.org/community/contributing.html)
- [ ] :computer: have added necessary unit/e2e tests.
- [ ] :notebook: have added documentation. Make sure [`MERMAID_RELEASE_VERSION`](https://mermaid.js.org/community/contributing.html#update-documentation) is used for all new features.
- [ ] :butterfly: If your PR makes a change that should be noted in one or more packages' changelogs, generate a changeset by running `pnpm changeset` and following the prompts. Changesets that add features should be `minor` and those that fix bugs should be `patch`. Please prefix changeset messages with `feat:`, `fix:`, or `chore:`.
````

## 8. Before you click "Create pull request"
Run the pre-PR checklist in your clone:
```bash
python <path-to>/contrib-buddy/scripts/precheck_pr.py --base upstream/develop
```

## Optional: claim the issue
Only if the project expects it (see rules above), post this yourself:

> Hi! I'd like to work on this issue. My plan is to <one line: what you will change>.
> 
> I expect to open a PR within <e.g. a week>. If someone else is already on it, or if you'd prefer a different approach, just let me know.
