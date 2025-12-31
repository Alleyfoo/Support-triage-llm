"""Final report generator using evidence bundles."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List

from . import config
from .validation import SchemaValidationError, validate_payload
from .triage_service import _extract_json_block, _call_ollama  # reuse helper


PROMPT_VERSION_REPORT = "report-v1"


def _allowed_tools_for_case(case_type: str) -> set[str]:
    if case_type == "incident":
        return {"log_evidence", "service_status", "logs", "app_events"}
    if case_type == "email_delivery":
        return {"fetch_email_events_sample", "dns_email_auth_check_sample", "email_events", "log_evidence"}
    return set()


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
    case_type = triage_json.get("case_type") or "unknown"
    allowed = _allowed_tools_for_case(case_type)
    if allowed:
        filtered = []
        for b in evidence_bundles:
            tool = (b.get("metadata") or {}).get("tool_name") or b.get("source")
            if tool in allowed:
                filtered.append(b)
        evidence_bundles = filtered
    if config.REPORT_MODE == "llm":
        return _generate_report_llm(triage_json, evidence_bundles)
    return _generate_report_template(triage_json, evidence_bundles)


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


def _generate_report_template(triage_json: Dict[str, Any], evidence_bundles: List[Dict[str, Any]]) -> Dict[str, Any]:
    classification = _classify(evidence_bundles)
    case_type = triage_json.get("case_type")
    kb_map = {
        "email_delivery": ["Email delivery troubleshooting"],
        "integration": ["Integration/webhook troubleshooting"],
        "auth_access": ["Login/MFA troubleshooting"],
        "ui_bug": ["UI issue reporting checklist"],
        "unknown": ["How to report an issue"],
    }
    kb_suggestions = kb_map.get(case_type, ["How to report an issue"])
    customer_tw = triage_json.get("customer_time_window") or {}
    report = {
        "classification": classification,
        "timeline_summary": _timeline(evidence_bundles),
        "customer_update": _customer_update(classification, evidence_bundles),
        "engineering_escalation": _engineering_escalation(classification, evidence_bundles),
        "kb_suggestions": kb_suggestions,
    }
    # Prepend customer-claimed window if present
    if customer_tw.get("start") or customer_tw.get("end"):
        cu = report["customer_update"]
        claimed = ""
        if customer_tw.get("start") and customer_tw.get("end"):
            claimed = f"Customer reports issues between {customer_tw['start']} and {customer_tw['end']} (UTC)."
        elif customer_tw.get("start"):
            claimed = f"Customer reports issues since {customer_tw['start']} (UTC)."
        if claimed and claimed not in cu["body"]:
            cu["body"] = f"{claimed}\n{cu['body']}".strip()
            report["customer_update"] = cu
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
        "report_mode": "template",
        "claim_warnings": warnings,
        "case_id": triage_json.get("_meta", {}).get("case_id"),
    }
    return report


def _generate_report_llm(triage_json: Dict[str, Any], evidence_bundles: List[Dict[str, Any]]) -> Dict[str, Any]:
    prompt_base = (
        "You are a precise support reporting assistant. Using ONLY the evidence bundles below, produce a JSON final report "
        "matching this schema:\n"
        f"{json.dumps(validate_payload.__defaults__, ensure_ascii=False) if False else ''}"
        "\nEvidence:\n"
        f"{json.dumps(evidence_bundles, ensure_ascii=False)}\n"
        "Rules:\n"
        "- Do not invent events. Cite event IDs/timestamps.\n"
        "- If evidence is missing, state uncertainty and what evidence is needed.\n"
        "- Never promise ETAs.\n"
        "- Return ONLY JSON.\n"
    )
    last_error = ""
    warnings: List[str] = []
    attempts = 0
    start = time.perf_counter()
    for attempt in range(2):
        attempts = attempt + 1
        prompt = prompt_base
        if last_error:
            prompt += f"Previous attempt failed validation: {last_error}. Fix and return ONLY valid JSON."
        try:
            raw = _call_ollama(prompt)
            parsed = _extract_json_block(raw)
            validate_payload(parsed, "final_report.schema.json")
            warnings.extend(_claim_checker(parsed, evidence_bundles))
            if warnings:
                raise SchemaValidationError("; ".join(warnings))
            meta = parsed.setdefault("_meta", {})
            meta.update(
                {
                    "prompt_version": PROMPT_VERSION_REPORT,
                    "report_mode": "llm",
                    "llm_latency_ms": int((time.perf_counter() - start) * 1000),
                    "llm_attempts": attempts,
                    "claim_warnings": warnings,
                    "case_id": triage_json.get("_meta", {}).get("case_id"),
                }
            )
            return parsed
        except (SchemaValidationError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            warnings.append(f"repair_attempt:{exc}")
            continue
        except Exception as exc:  # pragma: no cover - network errors
            last_error = str(exc)
            break

    # Fallback to template with warnings
    report = _generate_report_template(triage_json, evidence_bundles)
    meta = report.setdefault("_meta", {})
    meta["report_mode"] = "template_fallback"
    meta["claim_warnings"] = warnings + [f"llm_fallback:{last_error}"]
    return report
