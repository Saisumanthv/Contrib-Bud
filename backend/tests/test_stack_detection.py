"""Stack detection, doc discovery and rule extraction."""

from conftest import FIXTURES
from fetch_repo_docs import detect_stack, extract_rules, find_doc, find_pr_templates


def commands(items):
    return [i["command"] for i in items]


def test_pnpm_project_uses_package_scripts():
    paths = {"package.json", "pnpm-lock.yaml", "eslint.config.js"}
    files = {"package.json": (FIXTURES / "package.json").read_text()}
    stack = detect_stack(paths, files, {"TypeScript": 900, "JavaScript": 100})
    assert stack["package_managers"]["javascript"] == {"name": "pnpm", "source": "pnpm-lock.yaml"}
    assert commands(stack["install_commands"]) == ["pnpm install"]
    assert commands(stack["test_commands"]) == ["pnpm test"]
    assert "pnpm lint" in commands(stack["lint_commands"])
    assert "npx eslint ." not in commands(stack["lint_commands"])  # package script wins
    assert stack["languages"][0] == {"name": "TypeScript", "percent": 90.0}


def test_npm_placeholder_test_script_is_ignored():
    files = {"package.json": '{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1"}}'}
    stack = detect_stack({"package.json"}, files)
    assert stack["test_commands"] == []


def test_uv_python_project_prefixes_tools():
    pyproject = "[tool.pytest.ini_options]\ntestpaths=['tests']\n[tool.ruff]\nline-length=100\n"
    stack = detect_stack({"pyproject.toml", "uv.lock"}, {"pyproject.toml": pyproject})
    assert commands(stack["install_commands"]) == ["uv sync"]
    assert commands(stack["test_commands"]) == ["uv run pytest"]
    assert commands(stack["lint_commands"]) == ["uv run ruff check ."]


def test_requirements_txt_and_makefile():
    paths = {"requirements-dev.txt", "Makefile"}
    files = {"Makefile": "install:\n\tpip install -r requirements-dev.txt\ntest:\n\tpytest\nlint:\n\truff .\n"}
    stack = detect_stack(paths, files)
    assert commands(stack["install_commands"]) == ["pip install -r requirements-dev.txt"]
    assert "make test" in commands(stack["test_commands"])
    assert "make lint" in commands(stack["lint_commands"])


def test_polyglot_rust_and_go():
    stack = detect_stack({"Cargo.toml", "go.mod", ".golangci.yml"}, {})
    assert {"cargo test", "go test ./..."} <= set(commands(stack["test_commands"]))
    assert "golangci-lint run" in commands(stack["lint_commands"])


def test_ci_workflow_commands_are_collected_with_source():
    wf = "jobs:\n  t:\n    steps:\n      - run: npm ci\n      - run: npm test -- --coverage\n"
    stack = detect_stack({".github/workflows/ci.yml"}, {".github/workflows/ci.yml": wf})
    assert stack["ci_commands"] == [
        {"command": "npm test -- --coverage", "source": ".github/workflows/ci.yml"}
    ]


def test_find_docs_in_common_locations():
    paths = {".github/CONTRIBUTING.md", "docs/code_of_conduct.md",
             ".github/PULL_REQUEST_TEMPLATE/feature.md", "src/contributing.py"}
    assert find_doc(paths, "CONTRIBUTING") == ".github/CONTRIBUTING.md"
    assert find_doc(paths, "CODE_OF_CONDUCT") == "docs/code_of_conduct.md"
    assert find_pr_templates(paths) == [".github/PULL_REQUEST_TEMPLATE/feature.md"]


def test_extract_rules_with_evidence():
    contributing = (
        "# Contributing\n"
        "All commits must include a Signed-off-by line (DCO).\n"
        "You must sign our CLA before we can merge.\n"
        "We use Conventional Commits for messages.\n"
        "Please link to an issue in your PR description.\n"
        "Name branches like feature/my-thing or fix/123-bug.\n"
        "This repo is not about claws or clauses.\n"
    )
    rules = extract_rules({"CONTRIBUTING.md": contributing})
    assert rules["dco"]["source"] == "CONTRIBUTING.md"
    assert "Signed-off-by" in rules["dco"]["evidence"]
    assert "CLA" in rules["cla"]["evidence"]
    assert "conventional_commits" in rules
    assert "issue_link_required" in rules
    assert rules["branch_prefixes"]["value"] == ["feature", "fix"]


def test_extract_rules_avoids_false_positives_and_uses_config_files():
    rules = extract_rules({"README.md": "A declarative library. Said the email said."},
                          {"commitlint.config.js"})
    assert "cla" not in rules and "ai_policy" not in rules
    assert rules["conventional_commits"]["source"] == "commitlint.config.js"
