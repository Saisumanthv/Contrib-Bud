"""Open-weight model backends behind one tiny interface.

Select with BUDDY_PROVIDER:
  - "gemma"  (default): Gemma 4 via the Gemini API (needs GEMINI_API_KEY)
  - "ollama": any local open-weight model via the Ollama HTTP API (offline)
Override the model with BUDDY_MODEL.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

import requests

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_GEMMA_MODEL = "gemma-4-26b-a4b-it"  # per ai.google.dev/gemma/docs/core/gemma_on_gemini_api
DEFAULT_OLLAMA_MODEL = "gemma4"  # per ollama.com/library/gemma4
DEFAULT_OLLAMA_HOST = "http://localhost:11434"
TIMEOUT = 180


class ProviderError(Exception):
    """A user-facing model backend error with an actionable message."""


class Provider(Protocol):
    """Anything that turns (system, prompt) into text."""

    name: str
    model: str

    def generate(self, system: str, prompt: str, json_mode: bool = False) -> str:
        """Return the model's text response."""
        ...


@dataclass
class GemmaProvider:
    """Gemma 4 (open-weight) served through the Gemini API."""

    api_key: str
    model: str = DEFAULT_GEMMA_MODEL
    name: str = "gemma"

    def generate(self, system: str, prompt: str, json_mode: bool = False) -> str:
        """Call generateContent and return the non-thought text parts."""
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2 if json_mode else 0.4},
        }
        try:
            resp = requests.post(
                GEMINI_URL.format(model=self.model),
                headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                json=body, timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"Could not reach the Gemini API ({exc.__class__.__name__}). "
                                "Check your connection, or use BUDDY_PROVIDER=ollama.") from exc
        if resp.status_code in (400, 401, 403) and "API key" in resp.text:
            raise ProviderError("GEMINI_API_KEY was rejected. Create a new key at "
                                "https://aistudio.google.com/apikey.")
        if resp.status_code == 404:
            raise ProviderError(f"Model '{self.model}' was not found on the Gemini API. "
                                f"Unset BUDDY_MODEL to use the default ({DEFAULT_GEMMA_MODEL}).")
        if resp.status_code == 429:
            raise ProviderError("Gemini API quota/rate limit hit. Wait a minute and retry, "
                                "or use BUDDY_PROVIDER=ollama.")
        if resp.status_code >= 400:
            raise ProviderError(f"Gemini API error {resp.status_code}: {resp.text[:300]}")
        return _gemini_text(resp.json())


def _gemini_text(data: dict) -> str:
    """Extract answer text from a generateContent response, skipping thought parts."""
    candidates = data.get("candidates") or []
    if not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
        raise ProviderError(f"The model returned no answer ({reason}).")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text.strip():
        raise ProviderError("The model returned an empty answer. Try again.")
    return text


@dataclass
class OllamaProvider:
    """Any local open-weight model served by Ollama."""

    model: str = DEFAULT_OLLAMA_MODEL
    host: str = DEFAULT_OLLAMA_HOST
    name: str = "ollama"

    def generate(self, system: str, prompt: str, json_mode: bool = False) -> str:
        """Call /api/chat (non-streaming) and return the message content."""
        body: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0.2 if json_mode else 0.4, "num_ctx": 16384},
        }
        if json_mode:
            body["format"] = "json"
        try:
            resp = requests.post(f"{self.host.rstrip('/')}/api/chat", json=body, timeout=TIMEOUT)
        except requests.ConnectionError as exc:
            raise ProviderError(
                f"Ollama is not running at {self.host}. Install it from https://ollama.com, "
                f"then run: ollama serve  and  ollama pull {self.model}"
            ) from exc
        except requests.RequestException as exc:
            raise ProviderError(f"Ollama request failed ({exc.__class__.__name__}).") from exc
        if resp.status_code == 404:
            raise ProviderError(
                f"Model '{self.model}' is not pulled. Run: ollama pull {self.model}"
            )
        if resp.status_code >= 400:
            raise ProviderError(f"Ollama error {resp.status_code}: {resp.text[:300]}")
        content = (resp.json().get("message") or {}).get("content", "")
        if not content.strip():
            raise ProviderError("The local model returned an empty answer. Try again.")
        return content


def get_provider() -> Provider:
    """Build the provider selected by BUDDY_PROVIDER, validating its configuration."""
    choice = os.environ.get("BUDDY_PROVIDER", "gemma").strip().lower() or "gemma"
    model = os.environ.get("BUDDY_MODEL", "").strip()
    if choice == "gemma":
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            raise ProviderError(
                "GEMINI_API_KEY is not set. Get a free key at https://aistudio.google.com/apikey "
                "and set it (or add it to .env). Alternatives: BUDDY_PROVIDER=ollama for a local "
                "model, or --no-ai to see the facts only."
            )
        return GemmaProvider(api_key=key, model=model or DEFAULT_GEMMA_MODEL)
    if choice == "ollama":
        host = os.environ.get("OLLAMA_HOST", "").strip() or DEFAULT_OLLAMA_HOST
        if not host.startswith("http"):
            host = f"http://{host}"
        return OllamaProvider(model=model or DEFAULT_OLLAMA_MODEL, host=host)
    raise ProviderError(f"Unknown BUDDY_PROVIDER '{choice}'. Use 'gemma' or 'ollama'.")
