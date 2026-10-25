"""Codebase tour: areas, entry points, the plain tour and the model's checked tour."""

import json

from buddy import citations, rag, tour

FILES = [
    ("README.md", "# Proj\n\nA tiny CLI that greets people.\n"),
    ("pyproject.toml", '[project]\nname = "proj"\n[project.scripts]\nproj = "proj.cli:main"\n'),
    ("src/proj/cli.py", '"""Command-line entry point: parses arguments."""\nimport sys\n\n'
                        "def main():\n    print('hi')\n"),
    ("src/proj/greet.py", '"""Builds greetings."""\n\ndef greet(name):\n    return f"hi {name}"\n'),
    ("src/README.md", "# Source\n\nAll the Python code lives here.\n"),
    ("tests/test_greet.py", "from proj.greet import greet\n\ndef test_greet():\n    assert greet('a')\n"),
    ("docs/index.md", "# Docs\n"),
    (".github/workflows/ci.yml", "jobs: {}\n"),
    ("web/package.json", json.dumps({"main": "web/index.js"})),
    ("web/index.js", "// Serves the web page.\nconsole.log('up')\n"),
]
INDEX = rag.RepoIndex.build("o/proj", "main", FILES)


def test_areas_describe_each_folder():
    by_folder = {a["folder"]: a for a in tour.areas(sorted(INDEX.lines), INDEX)}
    src = by_folder["src"]
    assert src["description"] == "All the Python code lives here."
    assert src["languages"][0] == "Python" and src["kind"] == "code"
    assert src["sample"][0] == {"path": "src/proj/cli.py",
                                "about": "Command-line entry point: parses arguments."}
    assert by_folder["tests"]["kind"] == "tests" and by_folder["docs"]["kind"] == "docs"
    assert by_folder[".github"]["kind"] == "ci"
    assert list(by_folder)[-1] == "(root)"  # the root's own files come last


def test_entry_points_from_metadata_and_names():
    entries = [e["path"] for e in tour.entry_points(INDEX, None)]
    assert entries[0] == "src/proj/cli.py"  # from [project.scripts]
    assert "web/index.js" in entries
    assert not any(e.startswith("tests/") for e in entries)


def test_plain_tour_works_without_a_model():
    md = tour.plain_tour(tour.tour_facts(INDEX, {"description": "Greets people."}))
    assert "**src/**" in md and "`src/proj/cli.py`" in md and "tests/" in md


def test_run_with_a_model_checks_citations(monkeypatch):
    monkeypatch.setattr(tour.repo_docs, "fetch_repo_docs", lambda repo, **kw: {
        "repo": "o/proj", "default_branch": "main", "description": "x", "stack": {}})
    monkeypatch.setattr(tour.rag, "get_index", lambda o, r, ref: INDEX)

    class Model:
        name, model = "fake", "fake-model"

        def generate(self, system, prompt, json_mode=False):
            assert "[src/proj/cli.py:1-5]" in prompt  # entry point code is given
            return ("## How it's organized\n- The code lives in [src/] and greetings are built "
                    "by `greet` [src/proj/greet.py:3-4].\n- Settings are in [config/settings.py].")

    result = tour.run("o/proj", Model())
    assert result["ai"] and result["model"] == "fake-model"
    statuses = [c["status"] for c in result["citations"]]
    assert statuses == ["verified", "verified", "not_found"]
    assert result["citations"][0]["refs"][0]["kind"] == "folder"


def test_folder_citations_need_the_folder_to_exist():
    (ok,) = citations.verify("Code lives in [src/proj/].", INDEX)
    (bad,) = citations.verify("Code lives in [lib/].", INDEX)
    assert ok["status"] == "verified" and bad["status"] == "not_found"


def test_navigational_citations_only_need_to_exist():
    text = "Read the overview first [README.md]. See [o/proj] for more. Start in [nope.py]."
    nav = citations.Known(navigational=True, skip={"o/proj"})
    assert [c["status"] for c in citations.verify(text, INDEX, nav)] == ["verified", "not_found"]
    # In chat, the same whole-file claim must match the file's words.
    assert citations.verify(text, INDEX)[0]["status"] == "unsupported"


def test_samples_skip_package_markers():
    index = rag.RepoIndex.build("o/p", "main", [
        ("pkg/__init__.py", ""), ("pkg/core.py", '"""The core."""\n' + "x = 1\n" * 50)])
    (area,) = tour.areas(sorted(index.lines), index)
    assert area["sample"][0]["path"] == "pkg/core.py"
