# Contribution plan: $issue_title

**Issue:** [$repo#$issue_number]($issue_url) · **Labels:** $labels
**Branch:** `$branch` · **Base:** `$default_branch`

## 1. Fork, clone, branch
Fork the repo on GitHub first (Fork button on https://github.com/$repo), then:
```bash
git clone https://github.com/YOUR-USER/$repo_name.git
cd $repo_name
git remote add upstream https://github.com/$repo.git
git fetch upstream
git checkout -b $branch upstream/$default_branch
```

## 2. Set up and run the tests (before changing anything)
```bash
$setup_commands
$test_commands
```
$lint_section
## 3. Contribution rules for this repo
$rules

## 4. Likely files to look at
$likely_files

## 5. Approach
$approach

## 6. Commit
```bash
git add -p
git commit $commit_flags-m "$commit_message"
git push -u origin $branch
```

## 7. PR description draft
Open a PR from `$branch` against `$repo:$default_branch` and paste:

````markdown
$pr_description
````

## 8. Before you click "Create pull request"
Run the pre-PR checklist in your clone:
```bash
python <path-to>/contrib-buddy/scripts/precheck_pr.py --base upstream/$default_branch
```
