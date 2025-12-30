"""Triage service: redaction -> triage (heuristic or LLM) -> schema validation."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import config
from .redaction import redact
from .validation import SchemaValidationError, validate_payload, validate_with_retry
from .time_window import parse_time_window

PROMPT_VERSION_HEURISTIC = "triage-heuristic-v1"
PROMPT_VERSION_LLM = "triage-llm-v1"

DOMAIN_PATTERN = re.compile(r"\b([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b")
EMAIL_PATTERN = re.compile(r"\b[a-zA-Z0-9._%+-]+@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b")
SCHEMA_TEXT = (Path(__file__).resolve().parents[1] / "schemas" / "triage.schema.json").read_text(encoding="utf-8")
ALLOWED_TOP_KEYS = {
    "case_type",
    "severity",
    "time_window",
    "scope",
    "symptoms",
    "examples",
    "missing_info_questions",
    "suggested_tools",
    "draft_customer_reply",
}
ALLOWED_TOP_KEYS = {
    "case_type",
    "severity",
    "time_window",
    "scope",
    "symptoms",
    "examples",
    "missing_info_questions",
    "suggested_tools",
    "draft_customer_reply",
}


def _infer_case_type(text: str) -> str:
    lower = text.lower()
    if "webhook" in lower or "integration" in lower or "api" in lower or "sync" in lower or "connector" in lower:
        return "integration"
    if "bounce" in lower or "deliver" in lower or "email" in lower:
        return "email_delivery"
    if "ui" in lower or "button" in lower or "page" in lower:
        return "ui_bug"
    if "import" in lower:
        return "data_import"
    if "permission" in lower or "access" in lower:
        return "access_permissions"
    return "unknown"


def _infer_severity(text: str) -> str:
    lower = text.lower()
    if "critical" in lower or "urgent" in lower:
        return "critical"
    if any(token in lower for token in ["outage", "down"]):
        return "high"
    if "bounce" in lower or "bounced" in lower or "bouncing" in lower:
        return "high"
    if any(token in lower for token in ["failed", "failing", "error", "500"]):
        return "medium"
    if any(token in lower for token in ["degraded", "intermittent", "slow"]):
        return "medium"
    return "low"


def _detect_domains(text: str) -> List[str]:
    domains = set(match.group(1) for match in EMAIL_PATTERN.finditer(text))
    domains.update(DOMAIN_PATTERN.findall(text))
    return sorted(domains)


def _build_missing_questions(domains: List[str]) -> List[str]:
    questions = [
        "What time window is impacted (start/end in UTC)?",
        "How many users or recipients are affected?",
    ]
    if domains:
        questions.append(f"Are all recipients at {', '.join(domains)} affected?")
    questions.append("Have there been any recent config or provider changes?")
    return questions[:6]


def _suggest_tools(domains: List[str]) -> List[Dict[str, Any]]:
    tools: List[Dict[str, Any]] = [
        {"tool_name": "fetch_email_events", "reason": "Confirm bounce or delivery patterns", "params": {}},
    ]
    if domains:
        tools[0]["params"]["recipient_domain"] = domains[0]
        tools.append(
            {"tool_name": "dns_email_auth_check", "reason": "Check SPF/DKIM/DMARC presence", "params": {"domain": domains[0]}}
        )
    return tools


def _build_draft_reply(domains: List[str], severity: str) -> Dict[str, str]:
    domain_note = f" to {domains[0]}" if domains else ""
    subject = f"Quick update on your report{domain_note}"
    body_lines = [
        "Thanks for letting us know. We are reviewing the issue now.",
        f"Severity noted as {severity}.",
        "To help us investigate, could you share:",
        "- Impacted time window (UTC)",
        "- Affected users/recipients and examples",
        "- Any recent configuration or provider changes",
    ]
    if domains:
        body_lines.append(f"- Are other domains besides {domains[0]} affected?")
    return {"subject": subject, "body": "\n".join(body_lines)}


def _enrich_from_heuristic(text: str, payload: Dict[str, Any], metadata: Dict[str, Any]) -> Dict[str, Any]:
    """
    Use heuristic defaults to backfill LLM outputs when they are sparse or missing critical fields.
    """
    base = _base_triage_payload(text, metadata)
    # Backfill questions
    if not payload.get("missing_info_questions"):
        payload["missing_info_questions"] = base["missing_info_questions"]
    # Backfill draft
    dcr = payload.get("draft_customer_reply") or {}
    if not dcr.get("subject") or not dcr.get("body"):
        payload["draft_customer_reply"] = base["draft_customer_reply"]
    # Backfill scope/domains
    scope = payload.get("scope") or {}
    if not scope.get("recipient_domains"):
        scope["recipient_domains"] = base["scope"]["recipient_domains"]
    if "notes" not in scope:
        scope["notes"] = ""
    payload["scope"] = scope
    # Backfill symptoms/examples
    if not payload.get("symptoms"):
        payload["symptoms"] = base["symptoms"]
    if not payload.get("examples"):
        payload["examples"] = []
    # Backfill case type if unknown
    if payload.get("case_type") == "unknown" and base.get("case_type") != "unknown":
        payload["case_type"] = base["case_type"]
    # Backfill time window if missing
    if not payload.get("time_window") or (
        payload["time_window"].get("start") is None and payload["time_window"].get("end") is None
    ):
        payload["time_window"] = parse_time_window(text)
    return payload


def _base_triage_payload(text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    domains = _detect_domains(text)
    case_type = _infer_case_type(text)
    severity = _infer_severity(text)
    tenant_hint = metadata.get("tenant") or metadata.get("customer") or ""
    time_window = parse_time_window(text)

    return {
        "case_type": case_type,
        "severity": severity,
        "time_window": time_window,
        "scope": {
            "affected_tenants": [tenant_hint] if tenant_hint else [],
            "affected_users": [],
            "affected_recipients": [],
            "recipient_domains": domains,
            "is_all_users": False,
            "notes": "",
        },
        "symptoms": [text[:200]],
        "examples": [],
        "missing_info_questions": _build_missing_questions(domains),
        "suggested_tools": _suggest_tools(domains),
        "draft_customer_reply": _build_draft_reply(domains, severity),
    }


def _call_ollama(prompt: str) -> str:
    if not config.OLLAMA_MODEL:
        raise RuntimeError("OLLAMA_MODEL/MODEL_NAME not set")
    payload = {
        "model": config.OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": "You are a precise support triage assistant. Return only JSON matching the schema."},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {
            "temperature": float(config.TEMP),
            "num_predict": int(config.MAX_TOKENS),
        },
    }
    data = json.dumps(payload).encode("utf-8")
    url = config.OLLAMA_HOST.rstrip("/") + "/api/chat"
    request = Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=config.OLLAMA_TIMEOUT) as response:  # nosec - local inference endpoint
            body = response.read()
    except HTTPError as exc:
        if exc.code == 404:
            raise RuntimeError(
                f"Ollama 404 for model '{config.OLLAMA_MODEL}' at {config.OLLAMA_HOST}. "
                "Ensure the model name is correct and pulled (e.g., `ollama pull llama3.1:8b`) "
                "or set OLLAMA_URL/OLLAMA_MODEL appropriately."
            ) from exc
        raise
    parsed = json.loads(body)
    message = parsed.get("message", {})
    content = message.get("content")
    if not isinstance(content, str):
        raise RuntimeError("LLM returned empty content")
    return content


def _extract_json_block(text: str) -> Dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        snippet = text[start : end + 1]
        return json.loads(snippet)
    raise json.JSONDecodeError("Could not parse JSON", text, 0)


def _triage_heuristic(text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    triage_payload = _base_triage_payload(text, metadata)

    def _fix(payload: Dict[str, Any]) -> Dict[str, Any]:
        payload.setdefault("examples", [])
        payload.setdefault("suggested_tools", [])
        payload.setdefault("missing_info_questions", [])
        payload.setdefault("symptoms", [])
        return payload

    triage_payload = validate_with_retry(triage_payload, "triage.schema.json", fixer=_fix)
    triage_payload["_meta"] = {
        "llm_model": "heuristic",
        "prompt_version": PROMPT_VERSION_HEURISTIC,
        "triage_mode": "heuristic",
        "llm_latency_ms": 0,
        "llm_attempts": 0,
        "schema_valid": True,
    }
    return triage_payload


def _triage_llm(text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    prompt_base = (
        "Customer message:\n"
        f"{text}\n\n"
        "Return ONLY a JSON object matching this schema (no $schema/title keys, no prose, no schema echoes):\n"
        f"{SCHEMA_TEXT}\n"
    )
    last_error: str = ""
    attempts = 0
    start = time.perf_counter()
    for attempt in range(2):
        attempts = attempt + 1
        prompt = prompt_base
        if last_error:
            prompt += f"\nPrevious attempt failed schema validation: {last_error}\nReturn ONLY valid JSON."
        try:
            raw = _call_ollama(prompt)
            parsed = _extract_json_block(raw)
            for key in list(parsed.keys()):
                if key not in ALLOWED_TOP_KEYS:
                    parsed.pop(key, None)

            def _fix(payload: Dict[str, Any]) -> Dict[str, Any]:
                payload.setdefault("examples", [])
                payload.setdefault("suggested_tools", [])
                payload.setdefault("missing_info_questions", [])
                payload.setdefault("symptoms", [])
                payload.setdefault("time_window", {"start": None, "end": None, "confidence": 0.1})
                payload.setdefault("scope", {}).setdefault("notes", "")
                dcr = payload.setdefault("draft_customer_reply", {})
                dcr["subject"] = dcr.get("subject") or ""
                dcr["body"] = dcr.get("body") or ""
                return payload

            parsed = validate_with_retry(parsed, "triage.schema.json", fixer=_fix)
            latency_ms = int((time.perf_counter() - start) * 1000)
            parsed = _enrich_from_heuristic(text, parsed, metadata)
            parsed["_meta"] = {
                "llm_model": config.OLLAMA_MODEL or "ollama",
                "prompt_version": PROMPT_VERSION_LLM,
                "triage_mode": "llm",
                "llm_latency_ms": latency_ms,
                "llm_attempts": attempts,
                "schema_valid": True,
            }
            return parsed
        except (SchemaValidationError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            continue
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"LLM call failed: {exc}") from exc
    raise SchemaValidationError(last_error or "LLM could not produce schema-valid JSON")


def triage(raw_text: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Redact, extract triage fields, validate, and return triage JSON."""
    metadata = metadata or {}
    redaction = redact(raw_text)
    text = redaction["redacted_text"]

    mode = config.TRIAGE_MODE
    if mode == "llm":
        triage_payload = _triage_llm(text, metadata)
    else:
        triage_payload = _triage_heuristic(text, metadata)

    meta = triage_payload.get("_meta", {})
    meta["redaction_applied"] = redaction["redaction_applied"]
    meta["redacted_text"] = redaction["redacted_text"]
    meta["case_id"] = metadata.get("case_id")
    triage_payload["_meta"] = meta
    return triage_payload
