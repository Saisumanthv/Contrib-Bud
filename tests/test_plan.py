"""Branch names, commit messages, PR template filling and likely-file search."""

import plan_contribution as pc


def test_branch_name_from_labels_and_title():
    assert pc.make_branch_name(42, "Crash when config is empty", ["bug"]) == \
        "fix/42-crash-when-config-is-empty"
    assert pc.make_branch_name(7, "Typo in README", ["documentation"]) == "docs/7-typo-in-readme"
    assert pc.make_branch_name(9, "Add dark mode", ["enhancement"]) == "feat/9-add-dark-mode"


def test_branch_name_strips_prefixes_and_limits_length():
    name = pc.make_branch_name(5, "[Bug]: Something extremely long and wordy happens here again", [])
    assert name.startswith("fix/5-something-extremely")
    assert len(name.split("/", 1)[1]) <= len("5-") + 40
    assert pc.make_branch_name(6, "!!!", []) == "fix/6-change"


def test_branch_name_follows_repo_convention():
    assert pc.make_branch_name(3, "Add export", ["feature"], ["feature", "bugfix"]) == \
        "feature/3-add-export"
    assert pc.make_branch_name(4, "Crash", ["bug"], ["feature", "bugfix"]) == "bugfix/4-crash"


def test_commit_style_detection():
    conv = ["feat(ui): add button", "fix: handle null\n\nSigned-off-by: A <a@b.c>",
            "docs: update readme (#12)"]
    style = pc.detect_commit_style(conv)
    assert style["conventional"] is True
    assert style["signoff_ratio"] == round(1 / 3, 2)
    assert pc.detect_commit_style(["Update stuff", "Merge branch x"])["conventional"] is False
    assert pc.detect_commit_style([])["examples"] == []


def test_commit_message_matches_style_and_length():
    msg = pc.make_commit_message(12, "Crash when config is empty", ["bug"], {"conventional": True})
    assert msg == "fix: crash when config is empty (#12)"
    plain = pc.make_commit_message(12, "[Bug] crash when config is empty", [], {})
    assert plain == "Crash when config is empty (#12)"
    long = pc.make_commit_message(1, "x" * 200, [], {"conventional": True})
    assert len(long) <= 72 and long.endswith("(#1)")


def test_fill_repo_pr_template():
    template = "<!-- Describe your change -->\n## Summary\n\nFixes #\n\n## Testing\n"
    body = pc.fill_pr_template(template, 55, "Title", "pytest")
    assert "Fixes #55" in body
    assert "<!--" not in body

    zulip_style = "Fixes: \n\n**How changes were tested:**\n"
    assert pc.fill_pr_template(zulip_style, 9, "T", "").startswith("Fixes: #9")

    no_link = "## What\n\n## Why\n"
    assert pc.fill_pr_template(no_link, 3, "T", "").startswith("Fixes #3")


def test_default_pr_template_used_when_repo_has_none():
    body = pc.fill_pr_template(None, 8, "Fix crash", "npm test")
    assert body.startswith("Fixes #8")
    assert "`npm test` passes locally" in body


def test_likely_files_prefers_specific_matches():
    paths = {
        "src/widgets/config.py", "src/widgets/cli.py", "src/widgets/__init__.py",
        "tests/test_config.py", "docs/index.md", "node_modules/config/index.js",
        "package-lock.json",
    } | {f"src/widgets/mod{i}.py" for i in range(200)}
    text = "Crash in `load_config` when config.toml is empty. See src/widgets/config.py"
    found = pc.find_likely_files(text, paths)
    assert found[0]["path"] == "src/widgets/config.py"
    assert all("node_modules" not in f["path"] for f in found)
    assert all(f["path"] != "package-lock.json" for f in found)


def test_likely_files_prefers_source_over_fixtures_and_splits_camelcase():
    paths = {
        "packages/app/src/diagrams/gantt/ganttDb.ts",
        "e2e/diagrams/gantt/should-render-dates-with-dateformat-and-many-more-words.mmd",
        "docs/config/setup/gantt.md",
    }
    found = pc.find_likely_files("Gantt milestone task breaks with dateFormat", paths)
    assert found[0]["path"] == "packages/app/src/diagrams/gantt/ganttDb.ts"
    assert pc.path_tokens("src/ganttDb.ts") >= {"gantt", "db"}


def test_slug_drops_apostrophes():
    assert pc.slugify("Starting task doesn't work") == "starting-task-doesnt-work"
    assert pc.slugify("It’s broken") == "its-broken"


def test_extract_keywords_weights_and_stopwords():
    kw = pc.extract_keywords("The `parseConfig` function in utils/io.py should handle files")
    assert kw["utils/io.py"] == 10
    assert kw["parseconfig"] >= 3
    assert "the" not in kw and "should" not in kw
