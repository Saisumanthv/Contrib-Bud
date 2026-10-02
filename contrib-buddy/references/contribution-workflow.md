# Contribution workflow: fork → branch → commit → PR

Always prefer the repo's own CONTRIBUTING.md when it disagrees with this file.

## 1. Fork and clone
```bash
# Fork on GitHub first (the "Fork" button), then:
git clone https://github.com/YOUR-USER/REPO.git
cd REPO
git remote add upstream https://github.com/OWNER/REPO.git
git fetch upstream
```

## 2. Create a branch from the latest default branch
```bash
git checkout -b fix/123-short-description upstream/main
```
Never work on `main` directly. One branch per issue. Use the repo's naming convention if CONTRIBUTING.md defines one; otherwise `fix/`, `feat/`, `docs/`, `test/` + issue number + short slug.

## 3. Set up and verify the baseline
Install dependencies and run the test suite **before** changing anything, so you know which failures (if any) are pre-existing.

## 4. Make the change
- Keep it minimal and focused on the issue.
- Add or update tests that would have failed before your fix.
- Update docs/changelog if the project expects it.

## 5. Commit
```bash
git add -p                         # review each hunk
git commit -s -m "fix(parser): handle empty input (#123)"   # -s only if DCO is required
```
Match the project's commit style (check `git log --oneline -20`). Conventional Commits look like `type(scope): summary`.

## 6. Sync with upstream before pushing
```bash
git fetch upstream
git rebase upstream/main           # or merge, if the project prefers merges
```

## 7. Push and open the PR
```bash
git push -u origin fix/123-short-description
```
Then open the PR on GitHub. Fill **every** section of the PR template, and link the issue with `Fixes #123` (or `Closes #123`) so it auto-closes on merge.

## 8. Respond to review
```bash
# make the requested changes, then
git commit -m "address review: rename helper"
git push
```
Don't force-push during review unless the project asks you to squash. If asked to squash: `git rebase -i upstream/main`, then `git push --force-with-lease`.

## Troubleshooting
- **CI fails on something unrelated:** say so in a PR comment with a link to the failing job; don't try to fix unrelated code in the same PR.
- **Merge conflicts:** `git fetch upstream && git rebase upstream/main`, resolve, `git rebase --continue`, `git push --force-with-lease`.
- **DCO check fails:** `git commit --amend -s --no-edit` (last commit) or `git rebase --signoff upstream/main` (all commits), then `git push --force-with-lease`.
