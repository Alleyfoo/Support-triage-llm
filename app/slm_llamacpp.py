"""Wrapper utilities for llama-cpp based customer service replies."""

from __future__ import annotations

import json
from typing import Any, Dict, List

try:  # optional dependency
    from llama_cpp import Llama  # type: ignore
except Exception:  # pragma: no cover - llama_cpp is optional
    Llama = None  # type: ignore

SYSTEM = (
    "You are Aurora Gadgets' helpful customer service assistant. "
    "Always ground your answers in the provided knowledge base. "
    "Respond with JSON only."
)

JSON_START = "<JSON>"
JSON_END = "</JSON>"

TEMPLATES: Dict[str, str] = {
    "company_name": "Our company is called {value}.",
    "founded_year": "We were founded in {value}.",
    "headquarters": "Our headquarters are in {value}.",
    "support_hours": "Our support team is available {value}.",
    "warranty_policy": "Our warranty policy is {value}.",
    "return_policy": "Our return policy is {value}.",
    "shipping_time": "Shipping typically takes {value}.",
    "loyalty_program": "Regarding loyalty, {value}",
    "support_email": "You can reach us at {value}.",
    "premium_support": "For premium support, {value}",
    "account_regular_key": "Your regular account key is {value}.",
    "account_security_notice": "{value}",
    "account_identity_status": "{value}",
}


def _build_prompt(email_text: str, knowledge: Dict[str, str], expected_keys: List[str], language: str | None = None) -> str:
    """Return user prompt instructing the model to answer via JSON."""

    key_value_lines = "\n".join(f"- {key}: {knowledge.get(key, '')}" for key in sorted(knowledge))
    requested = ", ".join(expected_keys) if expected_keys else "all relevant"
    lang_map = {"fi": "Finnish", "sv": "Swedish", "se": "Swedish", "en": "English"}
    lang_line = ""
    if language:
        human = lang_map.get(str(language).lower())
        if human:
            lang_line = f"Please respond in {human}.\n"

    return (
        f"You are replying to a customer email.\n"
        f"Customer email:\n{email_text}\n\n"
        f"Knowledge base:\n{key_value_lines}\n\n"
        f"{lang_line}"
        f"Focus on answering the keys: {requested}."
        "Return JSON in the following shape:"
        f"{JSON_START}{{\"reply\":\"...\",\"answers\":{{\"key\":\"value\"}}}}{JSON_END}"
    )


def _stub_reply(email_text: str, knowledge: Dict[str, str], expected_keys: List[str]) -> Dict[str, Any]:
    """Deterministic fallback used when llama.cpp is unavailable."""

    keys = expected_keys or []
    if not keys:
        lower = email_text.lower()
        if "company" in lower:
            keys.append("company_name")
        if "founded" in lower or "established" in lower:
            keys.append("founded_year")
        if "where" in lower or "based" in lower:
            keys.append("headquarters")

    seen = []
    answers: Dict[str, str] = {}
    for key in keys:
        if key in seen:
            continue
        seen.append(key)
        value = knowledge.get(key)
        if value:
            answers[key] = value

    lines = [
        "Hello,",
        "Thanks for contacting Aurora Gadgets support."
    ]
    for key in seen:
        value = knowledge.get(key)
        if not value:
            continue
        template = TEMPLATES.get(key, "{value}")
        lines.append(template.format(value=value))

    lower_text = email_text.lower()
    if "secret key" in lower_text or "secret code" in lower_text:
        notice = knowledge.get("account_security_notice")
        if notice and notice not in lines:
            lines.append(notice)

    if not answers:
        lines.append("Let us know if you have any other questions about our services.")
    else:
        lines.append("Please let us know if you need any additional assistance.")
    reply = "\n".join(lines)
    return {"reply": reply, "answers": answers}


def _extract_json_block(text: str) -> Dict[str, Any]:
    """Extract a JSON object from text enclosed by sentinels."""

    start = text.find(JSON_START)
    end = text.rfind(JSON_END)
    if start == -1 or end == -1 or start >= end:
        raise ValueError("Sentinel JSON block not found")
    raw = text[start + len(JSON_START) : end].strip()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Model response must be an object")
    data.setdefault("reply", "")
    answers = data.get("answers", {})
    if not isinstance(answers, dict):
        answers = {}
    data["answers"] = {str(k): str(v) for k, v in answers.items()}
    return data


def generate_email_reply(
    email_text: str,
    knowledge: Dict[str, str],
    expected_keys: List[str],
    **kwargs: Any,
) -> Dict[str, Any]:
    """Generate an email reply using llama.cpp when available."""

    llama = kwargs.get("llama")
    temperature = kwargs.get("temperature", kwargs.get("temp", 0.0))
    max_tokens = kwargs.get("max_tokens", 512)

    if llama is None or not hasattr(llama, "create_chat_completion"):
        return _stub_reply(email_text, knowledge, expected_keys)

    prompt = _build_prompt(email_text, knowledge, expected_keys, language=kwargs.get("language"))
    try:  # pragma: no cover - requires llama_cpp
        result = llama.create_chat_completion(
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        content = result["choices"][0]["message"]["content"]
        return _extract_json_block(content)
    except Exception:  # pragma: no cover - fallback to stub on failure
        return _stub_reply(email_text, knowledge, expected_keys)


# Backwards compatibility alias for older imports
slm_cleanup = generate_email_reply

def build_prompt(email_text: str, knowledge: Dict[str, str], expected_keys: List[str], language: str | None = None) -> str:
    """Public wrapper around the prompt builder so other backends can reuse it."""

    return _build_prompt(email_text, knowledge, expected_keys, language=language)


def stub_reply(email_text: str, knowledge: Dict[str, str], expected_keys: List[str]) -> Dict[str, Any]:
    """Expose the deterministic fallback for alternative backends."""

    return _stub_reply(email_text, knowledge, expected_keys)


def extract_json_block(text: str) -> Dict[str, Any]:
    """Public wrapper around the sentinel JSON extractor."""

    return _extract_json_block(text)


