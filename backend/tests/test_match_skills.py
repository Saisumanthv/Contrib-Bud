"""Contribution types, technology detection and skill fit."""

import match_skills as ms


def issue(title, body="", labels=()):
    return {"title": title, "body": body, "labels": list(labels)}


def test_contribution_types_from_labels_then_titles():
    cases = [
        (issue("Update German strings", labels=["i18n"]), "translation"),
        (issue("Fix typo in installation guide"), "typo"),
        (issue("Document the --verbose flag", labels=["documentation"]), "docs"),
        (issue("Add tests for the date parser"), "tests"),
        (issue("README: broken link to the API docs"), "typo"),
        (issue("Crash when config is empty", labels=["bug"]), "code"),
        # A bug about the docs page is still a code change.
        (issue("Docs search crashes on empty query", labels=["bug"]), "code"),
    ]
    for item, expected in cases:
        result = ms.contribution_type(item)
        assert result["type"] == expected, item["title"]
        assert result["low_risk"] == (expected != "code")


def test_detect_techs_from_files_code_blocks_and_labels():
    assert set(
        ms.detect_techs(
            issue(
                "Button misaligned",
                "In `src/components/Nav.tsx` the padding is off, see `styles/nav.css`.",
            )
        )
    ) == {"TypeScript", "React", "CSS"}
    assert ms.detect_techs(issue("Parser fails", "```python\nparse('')\n```")) == ["Python"]
    assert "Rust" in ms.detect_techs(issue("Speed up loading", labels=["lang: rust"]))
    assert ms.detect_techs(issue("Fix typo in README")) == ["Markdown/Writing"]


def test_detect_techs_falls_back_to_the_repo_language():
    assert ms.detect_techs(issue("Make it faster"), ["Go", "Shell"]) == ["Go"]
    assert ms.detect_techs(issue("Make it faster"), ["Jupyter Notebook"]) == ["Python"]
    assert ms.detect_techs(issue("Make it faster")) == []


def test_normalize_skills_understands_common_spellings():
    assert ms.normalize_skills("js, React.js, golang, C++, Tailwind, Elixir, js") == [
        "JavaScript",
        "React",
        "Go",
        "C++",
        "CSS",
        "Elixir",
    ]
    assert ms.normalize_skills(["  python ", "", "TS"]) == ["Python", "TypeScript"]


def test_skill_fit():
    assert ms.skill_fit(["Python"], ["py"]) == {"fit": "good", "known": ["Python"], "missing": []}
    assert ms.skill_fit(["TypeScript", "React"], ["react"])["fit"] == "partial"
    assert ms.skill_fit(["Rust"], ["python"]) == {
        "fit": "stretch",
        "known": [],
        "missing": ["Rust"],
    }
    assert ms.skill_fit(["Markdown/Writing"], ["rust"])["fit"] == "good"  # anyone can write docs
    assert ms.skill_fit([], ["python"])["fit"] == "any"


def test_annotate_adds_everything():
    (item,) = ms.annotate([issue("Fix crash in api.py", labels=["bug"])], ["Python"], ["Python"])
    assert item["contribution"]["type"] == "code"
    assert item["techs"] == ["Python"] and item["skill_match"]["fit"] == "good"
    (plain,) = ms.annotate([issue("Typo")])
    assert "skill_match" not in plain


def test_links_and_lookalikes_are_not_technologies():
    body = "Repro: https://jsfiddle.net/abc/1 and https://mermaid.live/edit. Node C#1 in the graph."
    assert "C#" not in ms.detect_techs(issue("Support circular flow", body), ["TypeScript"])
    assert ms.detect_techs(issue("Port the CLI", "Rewrite it in .NET 8 with C#")) == ["C#"]
