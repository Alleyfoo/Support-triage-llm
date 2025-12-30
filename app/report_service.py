"""Final report generator using evidence bundles."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from . import config
from .validation import SchemaValidationError, validate_payload


PROMPT_VERSION_REPORT = "report-v1"


def _evidence_refs(bundles: List[Dict[str, Any]]) -> List[str]:
    refs: List[str] = []
    for bundle in bundles:
        for evt in bundle.get("events", []):
            if evt.get("id"):
                refs.append(str(evt["id"]))
            elif evt.get("ts"):
                refs.append(str(evt["ts"]))
    return refs


def _classify(bundles: List[Dict[str, Any]]) -> Dict[str, Any]:
    bounced = any(evt.get("type", "").lower().startswith("bounce") for b in bundles for evt in b.get("events", []))
    if bounced:
        return {"failure_stage": "recipient", "confidence": 0.6, "top_reasons": ["Recipient rejected or not found"]}
    unknown = not any(b.get("events") for b in bundles)
    if unknown:
        return {"failure_stage": "unknown", "confidence": 0.2, "top_reasons": ["No events observed"]}
    return {"failure_stage": "provider", "confidence": 0.4, "top_reasons": ["Provider issues suspected"]}


def _timeline(bundles: List[Dict[str, Any]]) -> str:
    lines = []
    for bundle in bundles:
        src = bundle.get("source", "unknown")
        for evt in bundle.get("events", []):
            ts = evt.get("ts", "")
            evt_id = evt.get("id", "")
            detail = evt.get("detail", "")
            lines.append(f"{ts} [{src}] ({evt_id}) {detail}")
    if not lines:
        return "No events found in evidence."
    return "\n".join(lines)


def _customer_update(classification: Dict[str, Any], bundles: List[Dict[str, Any]]) -> Dict[str, Any]:
    refs = _evidence_refs(bundles)
    subject = "Update on your report"
    body_lines = []
    if classification["failure_stage"] == "recipient":
        body_lines.append("We observed recipient-side bounces in the provided timeframe.")
    elif classification["failure_stage"] == "provider":
        body_lines.append("We observed provider-side anomalies and are investigating.")
    else:
        body_lines.append("We did not find clear failure signals in the evidence.")
    if refs:
        body_lines.append(f"Evidence references: {', '.join(refs[:5])}")
    body_lines.append("Next steps: we will continue monitoring and share updates.")
    return {
        "subject": subject,
        "body": "\n".join(body_lines),
        "requested_info": ["Additional examples with timestamps", "Any recent configuration changes"],
    }


def _engineering_escalation(classification: Dict[str, Any], bundles: List[Dict[str, Any]]) -> Dict[str, Any]:
    refs = _evidence_refs(bundles)
    body = _timeline(bundles)
    return {
        "title": f"Support triage escalation ({classification['failure_stage']})",
        "body": body,
        "evidence_refs": refs,
        "severity": "S2",
        "repro_steps": ["Review evidence timeline", "Attempt send to affected recipient if applicable"],
    }


def generate_report(triage_json: Dict[str, Any], evidence_bundles: List[Dict[str, Any]]) -> Dict[str, Any]:
    classification = _classify(evidence_bundles)
    report = {
        "classification": classification,
        "timeline_summary": _timeline(evidence_bundles),
        "customer_update": _customer_update(classification, evidence_bundles),
        "engineering_escalation": _engineering_escalation(classification, evidence_bundles),
        "kb_suggestions": ["Email delivery troubleshooting", "Recipient validation checklist"],
    }
    warnings: List[str] = []
    try:
        validate_payload(report, "final_report.schema.json")
    except SchemaValidationError as exc:
        report.setdefault("classification", classification)
        report.setdefault("customer_update", _customer_update(classification, evidence_bundles))
        report.setdefault("engineering_escalation", _engineering_escalation(classification, evidence_bundles))
        report.setdefault("kb_suggestions", ["Email delivery troubleshooting"])
        validate_payload(report, "final_report.schema.json")
        warnings.append(f"Schema repaired: {exc}")

    warnings.extend(_claim_checker(report, evidence_bundles))

    report["_meta"] = {
        "prompt_version": PROMPT_VERSION_REPORT,
        "report_mode": config.TRIAGE_MODE,
        "claim_warnings": warnings,
    }
    return report


def _claim_checker(report: Dict[str, Any], evidence_bundles: List[Dict[str, Any]]) -> List[str]:
    """Ensure claims are backed by evidence; return warnings."""
    warnings: List[str] = []
    text = json.dumps(report, ensure_ascii=False).lower()
    evidence_text = json.dumps(evidence_bundles, ensure_ascii=False).lower()

    checks = {
        "bounce": lambda: ("bounce" in evidence_text) or _count("bounced", evidence_bundles) > 0,
        "quarantine": lambda: "quarantine" in evidence_text,
        "dmarc": lambda: "dmarc" in evidence_text,
        "spf": lambda: "spf" in evidence_text,
        "rate limit": lambda: "rate limit" in evidence_text or "429" in evidence_text,
        "auth failed": lambda: "auth_failed" in evidence_text or "token expired" in evidence_text,
        "workflow disabled": lambda: "workflow_disabled" in evidence_text,
    }
    for keyword, predicate in checks.items():
        if keyword in text and not predicate():
            warnings.append(f"Claim '{keyword}' lacks evidence")
    return warnings


def _count(field: str, bundles: List[Dict[str, Any]]) -> int:
    total = 0
    for b in bundles:
        counts = b.get("summary_counts") or {}
        if field in counts:
            total += counts.get(field, 0) or 0
    return total
