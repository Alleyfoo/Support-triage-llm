"""Ollama-backed generation utilities."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .slm_llamacpp import (
    SYSTEM,
    build_prompt,
    extract_json_block,
    stub_reply,
)


def _parse_options(raw_options: Optional[str]) -> Dict[str, Any]:
    if not raw_options:
        return {}
    try:
        parsed = json.loads(raw_options)
    except json.JSONDecodeError:
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {}


def generate_email_reply_ollama(
    email_text: str,
    knowledge: Dict[str, str],
    expected_keys: List[str],
    *,
    model: Optional[str],
    host: str,
    temperature: float,
    max_tokens: int,
    raw_options: Optional[str] = None,
    timeout: float = 60.0,
    language: str | None = None,
) -> Dict[str, Any]:
    """Generate a reply using an Ollama-served model."""

    if not model:
        return stub_reply(email_text, knowledge, expected_keys)

    prompt = build_prompt(email_text, knowledge, expected_keys, language=language)
    payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
    }

    options: Dict[str, Any] = {
        "temperature": float(temperature),
        "num_predict": int(max_tokens),
    }
    extra = _parse_options(raw_options)
    if extra:
        options.update(extra)
    payload["options"] = options

    data = json.dumps(payload).encode("utf-8")
    url = host.rstrip("/") + "/api/chat"
    request = Request(url, data=data, headers={"Content-Type": "application/json"})

    try:
        with urlopen(request, timeout=timeout) as response:  # nosec - local inference endpoint
            body = response.read()
    except (HTTPError, URLError, TimeoutError):
        return stub_reply(email_text, knowledge, expected_keys)
    except OSError:
        return stub_reply(email_text, knowledge, expected_keys)

    try:
        result = json.loads(body)
        message = result.get("message", {})
        content = message.get("content")
        if not isinstance(content, str):
            return stub_reply(email_text, knowledge, expected_keys)
        return extract_json_block(content)
    except (json.JSONDecodeError, ValueError):
        return stub_reply(email_text, knowledge, expected_keys)


