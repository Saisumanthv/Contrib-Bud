"""Make the skill's scripts importable and block real network access in tests."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "contrib-buddy" / "scripts"))
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str):
    """Load a JSON fixture from tests/fixtures."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail loudly if any test tries to hit the network."""
    import requests

    def boom(*args, **kwargs):
        raise AssertionError(f"unexpected network call: {args[:1]}")

    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.setattr(requests, "post", boom)
    monkeypatch.setenv("CONTRIB_BUDDY_NO_CACHE", "1")
