"""Triage service: redaction -> heuristic triage -> schema validation."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .redaction import redact
from .validation import validate_with_retry

PROMPT_VERSION = "triage-v1"
LLM_MODEL = "heuristic"

DOMAIN_PATTERN = re.compile(r"\b([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b")
EMAIL_PATTERN = re.compile(r"\b[a-zA-Z0-9._%+-]+@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b")


def _infer_case_type(text: str) -> str:
    lower = text.lower()
    if "webhook" in lower or "integration" in lower or "api" in lower:
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


def triage(raw_text: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Redact, extract triage fields, validate, and return triage JSON."""
    metadata = metadata or {}
    redaction = redact(raw_text)
    text = redaction["redacted_text"]

    domains = _detect_domains(text)
    case_type = _infer_case_type(text)
    severity = _infer_severity(text)
    tenant_hint = metadata.get("tenant") or metadata.get("customer") or ""

    triage_payload = {
        "case_type": case_type,
        "severity": severity,
        "time_window": {"start": None, "end": None, "confidence": 0.1},
        "scope": {
            "affected_tenants": [tenant_hint] if tenant_hint else [],
            "affected_users": [],
            "affected_recipients": [],
            "recipient_domains": domains,
            "is_all_users": False,
            "notes": "",
        },
        "symptoms": [raw_text[:200]],
        "examples": [],
        "missing_info_questions": _build_missing_questions(domains),
        "suggested_tools": _suggest_tools(domains),
        "draft_customer_reply": _build_draft_reply(domains, severity),
    }

    def _fix(payload: Dict[str, Any]) -> Dict[str, Any]:
        # Ensure required arrays exist; keep simple for now.
        payload.setdefault("examples", [])
        payload.setdefault("suggested_tools", [])
        payload.setdefault("missing_info_questions", [])
        payload.setdefault("symptoms", [])
        return payload

    triage_payload = validate_with_retry(triage_payload, "triage.schema.json", fixer=_fix)
    triage_payload["_meta"] = {
        "llm_model": LLM_MODEL,
        "prompt_version": PROMPT_VERSION,
        "redaction_applied": redaction["redaction_applied"],
    }
    return triage_payload
