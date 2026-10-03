"""Prompt loading, structured (JSON) output parsing, validation and retry."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from string import Template
from typing import Any

from buddy.providers import Provider

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
Validator = Callable[[Any], list[str]]


class LLMError(Exception):
    """The model's output could not be used (e.g. invalid JSON after a retry)."""


def load_prompt(name: str, **values: Any) -> str:
    """Load prompts/<name>.txt and substitute $placeholders."""
    text = (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")
    return Template(text).safe_substitute({k: str(v) for k, v in values.items()})


def ask(provider: Provider, prompt_name: str, **values: Any) -> str:
    """Run a free-text prompt and return the model's Markdown."""
    return provider.generate(load_prompt("system"), load_prompt(prompt_name, **values)).strip()


def extract_json(text: str) -> Any:
    """Parse JSON from model output, tolerating code fences and surrounding prose."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except ValueError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except ValueError as exc:
            raise LLMError(f"invalid JSON: {exc}") from exc
    raise LLMError("no JSON object found in the response")


def ask_json(
    provider: Provider, prompt_name: str, validate: Validator, **values: Any
) -> dict[str, Any]:
    """Run a JSON prompt, validate the result, and retry once with the errors on failure."""
    system = load_prompt("system")
    prompt = load_prompt(prompt_name, **values)
    errors: list[str] = []
    for attempt in range(2):
        if attempt:
            prompt += (
                "\n\nYour previous answer was rejected: " + "; ".join(errors[:5])
                + ". Reply again with ONLY the corrected JSON object."
            )
        raw = provider.generate(system, prompt, json_mode=True)
        try:
            data = extract_json(raw)
        except LLMError as exc:
            errors = [str(exc)]
            continue
        errors = validate(data)
        if not errors:
            return data
    raise LLMError("model output failed validation after a retry: " + "; ".join(errors[:5]))


# --- validators ---------------------------------------------------------------

DIFFICULTIES = {"easy", "medium", "hard"}


def validate_ranking(valid_numbers: set[int]) -> Validator:
    """Build a validator for the issues-ranking JSON shape."""

    def check(data: Any) -> list[str]:
        errs: list[str] = []
        if not isinstance(data, dict):
            return ["top level must be a JSON object"]
        ranked = data.get("ranked")
        if not isinstance(ranked, list) or not ranked:
            errs.append("'ranked' must be a non-empty list")
            ranked = []
        seen: set[int] = set()
        for i, item in enumerate(ranked):
            if not isinstance(item, dict):
                errs.append(f"ranked[{i}] must be an object")
                continue
            num = item.get("number")
            if not isinstance(num, int) or num not in valid_numbers:
                errs.append(f"ranked[{i}].number {num!r} is not one of {sorted(valid_numbers)}")
            elif num in seen:
                errs.append(f"issue {num} appears twice")
            else:
                seen.add(num)
            if item.get("difficulty") not in DIFFICULTIES:
                errs.append(f"ranked[{i}].difficulty must be easy, medium or hard")
            for field in ("explanation", "why"):
                if not isinstance(item.get(field), str) or not item[field].strip():
                    errs.append(f"ranked[{i}].{field} must be a non-empty string")
        rec = data.get("recommendation")
        if not isinstance(rec, dict) or rec.get("number") not in valid_numbers:
            errs.append("'recommendation.number' must be one of the issue numbers")
        elif not isinstance(rec.get("reason"), str) or not rec["reason"].strip():
            errs.append("'recommendation.reason' must be a non-empty string")
        return errs

    return check


def validate_plan(data: Any) -> list[str]:
    """Validate the plan-refinement JSON shape."""
    if not isinstance(data, dict):
        return ["top level must be a JSON object"]
    return [
        f"'{f}' must be a non-empty string"
        for f in ("approach", "pr_summary", "claim_one_line")
        if not isinstance(data.get(f), str) or not data[f].strip()
    ]
