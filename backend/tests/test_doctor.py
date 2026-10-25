"""Setup doctor: version constraints, repo requirements and local checks."""

import json

import doctor as dr
import pytest


@pytest.mark.parametrize("version, spec, expected", [
    ((20, 11, 0), ">=18", True),
    ((16, 0, 0), ">=18", False),
    ((18, 19, 1), "^18.17.0", True),
    ((19, 0, 0), "^18.17.0", False),
    ((0, 4, 9), "^0.4.2", True),
    ((0, 5, 0), "^0.4.2", False),
    ((1, 2, 9), "~1.2.0", True),
    ((1, 3, 0), "~1.2", False),
    ((20, 1, 0), "18.x || >=20", True),
    ((19, 0, 0), "18.x || >=20", False),
    ((18, 5, 0), ">=18 <21", True),
    ((21, 0, 0), ">=18 <21", False),
    ((3, 12, 1), ">=3.10,<4", True),
    ((3, 9, 7), ">=3.10,<4", False),
    ((3, 13, 0), "~=3.11", True),
    ((4, 0, 0), "~=3.11", False),
    ((3, 11, 9), "~=3.11.2", True),
    ((3, 12, 0), "~=3.11.2", False),
    ((3, 11, 2), "==3.11.*", True),
    ((3, 11, 2), "3.11", True),
    ((3, 12, 0), "3.11", False),
    ((20, 0, 0), "*", True),
    ((20, 0, 0), "lts/*", None),
    (None, ">=18", None),
])
def test_satisfies(version, spec, expected):
    assert dr.satisfies(version, spec) is expected


def test_parse_version_from_tool_output():
    assert dr.parse_version("v20.11.0") == (20, 11, 0)
    assert dr.parse_version("Python 3.12.1") == (3, 12, 1)
    assert dr.parse_version("uv 0.4.18 (abc 2024-10-01)") == (0, 4, 18)
    assert dr.parse_version("no digits") is None


def test_requirements_from_repo_files():
    files = {
        "package.json": json.dumps({"engines": {"node": ">=18"}, "packageManager": "pnpm@9.1.0+sha",
                                    "scripts": {"test": "vitest", "lint": "eslint ."}}),
        "pnpm-lock.yaml": "",
        ".nvmrc": "v20\n",
        "pyproject.toml": '[project]\nname = "x"\nrequires-python = ">=3.10"\n',
        "go.mod": "module x\n\ngo 1.22\n",
        ".tool-versions": "nodejs 20.11.0\nruby 3.2.2\n",
    }
    reqs = {(r["tool"], r["required"], r["kind"]): r["source"] for r in dr.requirements(files)}
    assert reqs[("node", ">=18", "range")] == "package.json (engines)"
    assert reqs[("pnpm", "9.1.0", "pin")] == "package.json (packageManager)"
    assert reqs[("node", "20", "pin")] == ".nvmrc"
    assert reqs[("python", ">=3.10", "range")] == "pyproject.toml (requires-python)"
    assert reqs[("go", ">=1.22", "range")] == "go.mod"
    assert reqs[("ruby", "3.2.2", "pin")] == ".tool-versions"
    assert any(t == "git" for t, _, _ in reqs)


def test_tools_come_from_documented_commands():
    files = {"package.json": json.dumps({"scripts": {"test": "jest"}}), "package-lock.json": ""}
    tools = {r["tool"]: r for r in dr.requirements(files)}
    assert tools["npm"]["kind"] == "tool" and tools["node"]["source"] == "needed by npm"


def test_assess_statuses():
    node = dr._req("node", ">=18", "package.json (engines)")
    assert dr.assess(node, "v20.1.0", (20, 1, 0))["status"] == "pass"
    old = dr.assess(node, "v16.0.0", (16, 0, 0))
    assert old["status"] == "fail" and "Need node >=18" in old["fix"]
    assert dr.assess(node, None, None)["status"] == "missing"
    pin = dr._req("node", "20", ".nvmrc", "pin")
    assert dr.assess(pin, "v20.9.0", (20, 9, 0))["status"] == "pass"
    warn = dr.assess(pin, "v22.0.0", (22, 0, 0))
    assert warn["status"] == "warn" and "nvm use" in warn["fix"]
    py = dr._req("python", "3.11", ".python-version", "pin")
    assert dr.assess(py, "Python 3.12.0", (3, 12, 0))["status"] == "warn"  # minor matters


def test_doctor_on_a_folder(monkeypatch):
    import tempfile
    from pathlib import Path

    root = Path(tempfile.mkdtemp(prefix="doctor-test-"))
    (root / "package.json").write_text(json.dumps({"engines": {"node": ">=18"}}))
    (root / "package-lock.json").write_text("{}")
    versions = {"node": ("v16.20.0", (16, 20, 0)), "npm": ("8.19.4", (8, 19, 4)),
                "git": ("git version 2.44.0", (2, 44, 0))}
    monkeypatch.setattr(dr, "installed_version", lambda tool: versions.get(tool, (None, None)))
    result = dr.doctor(str(root))
    assert not result["ready"]
    assert result["checks"][0]["tool"] == "node" and result["checks"][0]["status"] == "fail"
    assert "Need node >=18" in dr.to_markdown(result)
