"""Structured-output parsing for LLM responses."""

import json
import re

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_json_object(text: str) -> dict:
    """Parse a JSON object from model text, tolerating a fenced code block."""
    if not text or not text.strip():
        raise ValueError("Model returned an empty response")
    candidate = text.strip()
    fenced = _FENCE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as error:
        raise ValueError(f"Model output is not valid JSON: {error}") from None
    if not isinstance(data, dict):
        raise ValueError("Model output must be a JSON object")
    return data
