# Contributing to contrib-buddy

Thanks for helping! This project exists to make first contributions easier, so we try to be the kind of repo we'd want to contribute to.

## Ground rules

- **Open or comment on an issue first** for anything bigger than a typo, so we can agree on the approach.
- **No need to ask to be assigned.** Comment "I'm working on this" so others know, then open a draft PR within a week or so.
- **Keep PRs small and focused**, with one change per PR.
- **Link the issue** in your PR description (`Fixes #123`).
- **AI-assisted contributions are welcome**, but you must understand, run and test every line you submit.

## Setup

```bash
git clone https://github.com/YOUR-USER/contrib-buddy && cd contrib-buddy
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Before opening a PR

```bash
ruff check .
pytest -q
```

Or dogfood it: `buddy check --pr-body my-pr.md`.

- Use [Conventional Commits](https://www.conventionalcommits.org): `feat(scripts): …`, `fix(cli): …`, `docs: …`, `test: …`.
- Branch names: `feat/<issue>-short-name`, `fix/<issue>-short-name`, `docs/<issue>-short-name`.
- Tests must not hit the network. `tests/conftest.py` blocks it, so use fixtures in `tests/fixtures/`.
- If you change `contrib-buddy/SKILL.md`, keep it valid: `pip install skills-ref`, then run the validation step from `.github/workflows/ci.yml`.

## Project layout

| Path | What lives there |
|---|---|
| `contrib-buddy/` | The Agent Skill: `SKILL.md`, `scripts/`, `references/`, `assets/` |
| `buddy/` | The CLI, model providers, and `prompts/*.txt` |
| `tests/` | pytest suite with fixtures |
| `demo/` | Demo script and sample output |

Scripts in `contrib-buddy/scripts/` must stay dependency-light (standard library + `requests`) so agents can run them anywhere.

## Good first issues

These are well-scoped starter tasks. Comment on the matching GitHub issue (or open one) before starting:

1. **Add `--since` to `find_issues.py`** to only show issues updated in the last N days. Touches `contrib-buddy/scripts/find_issues.py` and `tests/test_find_issues.py`.
2. **Detect more lint configs** (`biome.json`, `.rubocop.yml`, `.swiftlint.yml`) in `detect_stack()`. Touches `contrib-buddy/scripts/fetch_repo_docs.py` and `tests/test_stack_detection.py`.
3. **Add more claim phrases** to `CLAIM_RE` ("I can take this", "on it", "assign to me please") with tests. Touches `contrib-buddy/scripts/find_issues.py`.
4. **`buddy plan --open-issue`**: print the issue URL as a clickable link and show the issue's top 3 most recent comments. Touches `buddy/cli.py`.

## Code of conduct

Be kind and assume good intent. Harassment of any kind is not tolerated. Maintainers may remove comments or contributors who are not respectful.
