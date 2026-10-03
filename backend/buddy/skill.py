"""Bridge to the agent skill's helper scripts, so the CLI and agents share one source of facts."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

SKILL_DIR = Path(__file__).resolve().parent.parent / "contrib-buddy"
SCRIPTS_DIR = SKILL_DIR / "scripts"


def _load(name: str) -> ModuleType:
    if not SCRIPTS_DIR.is_dir():
        raise RuntimeError(
            f"Skill scripts not found at {SCRIPTS_DIR}. Install from a clone with: pip install -e ."
        )
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    return importlib.import_module(name)


github = _load("_github")
repo_docs = _load("fetch_repo_docs")
issues = _load("find_issues")
ranker = _load("rank_issues")
planner = _load("plan_contribution")
prechecker = _load("precheck_pr")

GitHubError = github.GitHubError
