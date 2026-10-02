"""Structured output parsing, validation/retry, and provider plumbing (network mocked)."""

import pytest
import requests

from buddy import llm, providers
from buddy.cli import merge_ranking


class FakeProvider:
    name, model = "fake", "fake-model"

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def generate(self, system, prompt, json_mode=False):
        self.prompts.append(prompt)
        return self.replies.pop(0)


GOOD = ('{"ranked": [{"number": 1, "difficulty": "easy", "explanation": "Fix typo [#1]", '
        '"why": "tiny"}], "recommendation": {"number": 1, "reason": "Smallest [#1]"}}')


def test_extract_json_handles_fences_and_prose():
    assert llm.extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert llm.extract_json('Sure! Here you go: {"a": [1, 2]} Hope it helps.') == {"a": [1, 2]}
    with pytest.raises(llm.LLMError):
        llm.extract_json("no json at all")


def test_validate_ranking_catches_bad_numbers_and_fields():
    check = llm.validate_ranking({1, 2})
    assert check(llm.extract_json(GOOD)) == []
    errors = check({"ranked": [{"number": 99, "difficulty": "trivial"}], "recommendation": {}})
    joined = "; ".join(errors)
    assert "99" in joined and "difficulty" in joined and "explanation" in joined
    assert "recommendation" in joined


def test_ask_json_retries_once_with_errors():
    fake = FakeProvider(["{not json", GOOD])
    data = llm.ask_json(fake, "issues", llm.validate_ranking({1}), repo="a/b", facts="[]")
    assert data["recommendation"]["number"] == 1
    assert len(fake.prompts) == 2
    assert "rejected" in fake.prompts[1]


def test_ask_json_gives_up_after_retry():
    fake = FakeProvider(["{}", "{}"])
    with pytest.raises(llm.LLMError):
        llm.ask_json(fake, "plan", llm.validate_plan, repo="a/b", number=1, facts="{}")


def test_prompts_exist_and_substitute():
    text = llm.load_prompt("plan", repo="acme/widgets", number=5, facts="{}")
    assert "acme/widgets" in text and "#5" in text and "$" not in text.replace("$ ", "")


def test_merge_ranking_reorders_and_keeps_missing():
    ranked = [{"number": 1}, {"number": 2}, {"number": 3}]
    ai = {"ranked": [{"number": 3, "why": "x"}, {"number": 1, "why": "y"}]}
    assert [i["number"] for i in merge_ranking(ranked, ai)] == [3, 1, 2]
    assert merge_ranking(ranked, None) == ranked


class FakeResponse:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._payload, self.text = status, payload, text

    def json(self):
        return self._payload


def test_gemma_provider_skips_thought_parts(monkeypatch):
    payload = {"candidates": [{"content": {"parts": [
        {"text": "thinking...", "thought": True}, {"text": "Final answer"}]}}]}
    calls = {}

    def fake_post(url, headers, json, timeout):
        calls.update(url=url, headers=headers, body=json)
        return FakeResponse(200, payload)

    monkeypatch.setattr(requests, "post", fake_post)
    out = providers.GemmaProvider(api_key="k").generate("sys", "hi")
    assert out == "Final answer"
    assert "gemma-4-26b-a4b-it:generateContent" in calls["url"]
    assert calls["headers"]["x-goog-api-key"] == "k"
    assert calls["body"]["systemInstruction"]["parts"][0]["text"] == "sys"


def test_gemma_provider_friendly_errors(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(429, text="quota"))
    with pytest.raises(providers.ProviderError, match="quota"):
        providers.GemmaProvider(api_key="k").generate("s", "p")


def test_ollama_provider_not_running(monkeypatch):
    def refuse(*a, **k):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "post", refuse)
    with pytest.raises(providers.ProviderError, match="ollama pull gemma4"):
        providers.OllamaProvider().generate("s", "p")


def test_ollama_provider_json_mode(monkeypatch):
    seen = {}

    def fake_post(url, json, timeout):
        seen.update(url=url, body=json)
        return FakeResponse(200, {"message": {"content": '{"ok": true}'}})

    monkeypatch.setattr(requests, "post", fake_post)
    out = providers.OllamaProvider(model="qwen3", host="http://h:1").generate("s", "p", True)
    assert out == '{"ok": true}'
    assert seen["url"] == "http://h:1/api/chat"
    assert seen["body"]["format"] == "json" and seen["body"]["model"] == "qwen3"


def test_get_provider_selection(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("BUDDY_MODEL", raising=False)
    monkeypatch.setenv("BUDDY_PROVIDER", "gemma")
    with pytest.raises(providers.ProviderError, match="GEMINI_API_KEY"):
        providers.get_provider()
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert providers.get_provider().model == providers.DEFAULT_GEMMA_MODEL
    monkeypatch.setenv("BUDDY_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:9999")
    p = providers.get_provider()
    assert p.name == "ollama" and p.host == "http://127.0.0.1:9999"
    monkeypatch.setenv("BUDDY_PROVIDER", "gpt")
    with pytest.raises(providers.ProviderError, match="Unknown"):
        providers.get_provider()
