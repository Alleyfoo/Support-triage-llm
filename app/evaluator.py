"""Semantic evaluator for question/answer pairs.

Tries to use the local Ollama backend if configured; otherwise falls back to a
deterministic heuristic stub. Returns a dict with fields:
  - score: float in [0,1]
  - addresses_question: bool
  - issues: list[str]
  - explanation: str
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import MODEL_BACKEND, OLLAMA_HOST, OLLAMA_MODEL, OLLAMA_TIMEOUT


def _stub_evaluate(question: str, answer: str) -> Dict[str, Any]:
    q = (question or "").strip().lower()
    a = (answer or "").strip().lower()
    if not a:
        return {
            "score": 0.0,
            "addresses_question": False,
            "issues": ["empty_reply"],
            "explanation": "No reply generated",
        }
    overlap = len(set(q.split()) & set(a.split()))
    score = min(1.0, 0.2 + 0.1 * overlap)
    return {
        "score": round(score, 2),
        "addresses_question": score >= 0.5,
        "issues": [] if score >= 0.5 else ["low_overlap"],
        "explanation": "Heuristic keyword overlap",
    }


def evaluate_qa(question: str, answer: str, *, language: Optional[str] = None, timeout: Optional[float] = None) -> Dict[str, Any]:
    if MODEL_BACKEND != "ollama" or not OLLAMA_MODEL:
        return _stub_evaluate(question, answer)

    lang_map = {"fi": "Finnish", "sv": "Swedish", "se": "Swedish", "en": "English"}
    lang_hint = lang_map.get((language or "").lower())
    system = (
        "You are a strict evaluator for customer service QA. "
        "Given a customer email and a drafted reply, decide if the reply addresses the question. "
        "Respond with a compact JSON object."
    )
    if lang_hint:
        system += f" The evaluation language is {lang_hint}."

    user = (
        "Email: \n" + question + "\n\nReply:\n" + answer + "\n\n"
        "Output JSON with fields: score (0..1), addresses_question (bool), issues (list of short tags), explanation (short)."
    )

    payload: Dict[str, Any] = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
    }
    data = json.dumps(payload).encode("utf-8")
    url = OLLAMA_HOST.rstrip("/") + "/api/chat"
    request = Request(url, data=data, headers={"Content-Type": "application/json"})

    try:
        with urlopen(request, timeout=timeout or OLLAMA_TIMEOUT) as response:  # nosec - local endpoint
            body = response.read()
    except (HTTPError, URLError, TimeoutError, OSError):
        return _stub_evaluate(question, answer)

    try:
        result = json.loads(body)
        content = (result.get("message") or {}).get("content")
        data = json.loads(content) if isinstance(content, str) else None
        if not isinstance(data, dict):
            return _stub_evaluate(question, answer)
        score = float(data.get("score", 0.0))
        addr = bool(data.get("addresses_question", score >= 0.5))
        issues = data.get("issues")
        if not isinstance(issues, list):
            issues = []
        explanation = str(data.get("explanation", "")).strip()
        return {
            "score": max(0.0, min(1.0, score)),
            "addresses_question": addr,
            "issues": [str(x) for x in issues],
            "explanation": explanation,
        }
    except Exception:
        return _stub_evaluate(question, answer)

