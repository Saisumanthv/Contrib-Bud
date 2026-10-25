"""GitHub requests survive a flaky connection: timeouts and resets are retried."""

import _github as gh
import pytest
import requests


class FakeResponse:
    def __init__(self, status=200, data=None):
        self.status_code, self._data, self.headers, self.text = status, data or {}, {}, ""
        self.encoding = "utf-8"

    def json(self):
        return self._data


class FlakySession:
    """Fails with each item of `failures` in turn, then answers 200."""

    def __init__(self, *failures):
        self.failures, self.calls = list(failures), 0

    def get(self, url, **kwargs):
        self.calls += 1
        if self.failures:
            failure = self.failures.pop(0)
            if isinstance(failure, int):
                return FakeResponse(failure)
            raise failure
        return FakeResponse(200, {"ok": True})


@pytest.fixture
def session(monkeypatch):
    sleeps = []
    monkeypatch.setattr(gh.time, "sleep", sleeps.append)

    def use(*failures):
        fake = FlakySession(*failures)
        monkeypatch.setattr(gh, "_session", lambda: fake)
        return fake, sleeps

    return use


def test_timeouts_and_resets_are_retried(session):
    fake, sleeps = session(requests.ReadTimeout("stalled"), requests.ConnectionError("reset"))
    assert gh.api_get("/repos/o/r") == {"ok": True}
    assert fake.calls == 3 and sleeps == [2, 5]


def test_github_server_hiccups_are_retried(session):
    fake, _ = session(503)
    assert gh.api_get("/repos/o/r") == {"ok": True} and fake.calls == 2


def test_gives_up_with_a_clear_message(session):
    fake, _ = session(*[requests.ReadTimeout("stalled")] * 4)
    with pytest.raises(gh.GitHubError, match="even after 4 tries"):
        gh.api_get("/repos/o/r")
    assert fake.calls == 4


def test_real_errors_are_not_retried(session):
    fake, sleeps = session(404)
    with pytest.raises(gh.GitHubError, match="Not found"):
        gh.api_get("/repos/o/missing")
    assert fake.calls == 1 and sleeps == []


def test_raw_files_retry_and_then_give_up_quietly(session):
    fake, _ = session(requests.ConnectTimeout("slow"))
    assert gh.raw_get("o", "r", "main", "README.md") is not None and fake.calls == 2
    session(*[requests.ConnectionError("down")] * 4)
    assert gh.raw_get("o", "r", "main", "README.md") is None


def test_unreachable_github_falls_back_to_an_older_cached_answer(session, monkeypatch):
    import os
    import tempfile
    import time
    from pathlib import Path

    tmp_path = Path(tempfile.mkdtemp(prefix="contrib-buddy-test-"))

    monkeypatch.delenv("CONTRIB_BUDDY_NO_CACHE")
    monkeypatch.setattr(gh, "CACHE_DIR", tmp_path)
    fake, _ = session()
    assert gh.api_get("/repos/o/r") == {"ok": True}  # cached now
    (cached,) = tmp_path.iterdir()
    hour_ago = time.time() - 3600  # past the 15-minute freshness window
    os.utime(cached, (hour_ago, hour_ago))
    session(*[requests.ConnectTimeout("down")] * 4)
    assert gh.api_get("/repos/o/r") == {"ok": True}
    day_ago = time.time() - 2 * 86400
    os.utime(cached, (day_ago, day_ago))
    session(*[requests.ConnectTimeout("down")] * 4)
    with pytest.raises(gh.GitHubError):  # too old to trust
        gh.api_get("/repos/o/r")
