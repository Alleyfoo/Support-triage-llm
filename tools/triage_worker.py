#!/usr/bin/env python3
"""SQLite-backed worker that performs triage instead of chatbot replies."""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4
import hashlib

from app import knowledge, queue_db
from app.triage_service import triage
from app.validation import SchemaValidationError
from app import report_service, config, metrics
from tools import registry
from tools import evidence_runner

EXPECTED_TOOLS_BY_CASE = {
    # Legacy fallback when LLM does not suggest anything valid.
    "email_delivery": {"fetch_email_events_sample", "dns_email_auth_check_sample"},
    "integration": {"fetch_integration_events_sample"},
    "auth_access": {"fetch_app_events_sample"},
    "ui_bug": {"fetch_app_events_sample"},
}


def _has_outage_language(triage_result: Dict[str, Any]) -> bool:
    text_parts: List[str] = []
    for field in ["symptoms"]:
        vals = triage_result.get(field) or []
        if isinstance(vals, list):
            text_parts.extend([str(v) for v in vals])
    draft = triage_result.get("draft_customer_reply", {})
    if isinstance(draft, dict):
        text_parts.append(draft.get("body") or "")
    text = " ".join(text_parts).lower()
    keywords = ["down", "outage", "unavailable", "downtime", "cannot access", "unresponsive", "timeout"]
    return any(k in text for k in keywords)


def _should_run_log_tool(triage_result: Dict[str, Any]) -> bool:
    if triage_result.get("case_type") == "incident":
        return True
    return _has_outage_language(triage_result)


def _allowed_tools(triage_result: Dict[str, Any]) -> set[str]:
    case_type = triage_result.get("case_type")
    outage = _has_outage_language(triage_result)
    allowed: set[str] = set()
    if case_type == "incident":
        allowed.update({"log_evidence", "service_status"})
    elif case_type == "email_delivery":
        allowed.update({"fetch_email_events_sample", "dns_email_auth_check_sample"})
        if outage:
            allowed.add("log_evidence")
    else:
        if outage:
            allowed.add("log_evidence")
    return allowed


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _extract_text(row: Dict[str, Any]) -> str:
    return str(
        row.get("payload")
        or row.get("raw_payload")
        or row.get("body")
        or row.get("text")
        or ""
    ).strip()


def _extract_request_ids(text: str) -> Dict[str, Optional[str]]:
    request_ids: List[str] = []
    error_codes: List[str] = []
    uuid_re = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
    reqid_re = re.compile(r"(x-request-id|request id|reqid|traceparent|trace-id)\s*[:=]\s*([A-Za-z0-9-]{6,128})", re.IGNORECASE)
    err_re = re.compile(r"\b(ERR[-_]?[A-Z0-9]{3,10}|\d{3,5})\b")
    for m in reqid_re.finditer(text):
        request_ids.append(m.group(2))
    for m in uuid_re.finditer(text):
        request_ids.append(m.group(0))
    for m in err_re.finditer(text):
        error_codes.append(m.group(1))
    # dedupe and cap
    def _dedupe_cap(values: List[str], cap: int = 10) -> List[str]:
        seen = []
        for v in values:
            if v not in seen:
                seen.append(v)
            if len(seen) >= cap:
                break
        return seen
    return {"customer_request_ids": _dedupe_cap(request_ids), "error_codes": _dedupe_cap(error_codes)}


def _backoff_seconds(retry_count: int) -> int:
    base = max(config.RETRY_BASE_SECONDS, 1)
    max_wait = max(config.RETRY_MAX_SECONDS, base)
    return min(int(base * (2**retry_count)), max_wait)


def _derive_query_time_window(triage_result: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, Any]:
    anchor_iso = meta.get("time_window_anchor")
    try:
        anchor_dt = _parse_iso(anchor_iso) if anchor_iso else datetime.now(timezone.utc)
    except Exception:
        anchor_dt = datetime.now(timezone.utc)

    tw = triage_result.get("time_window") or {}
    start = tw.get("start")
    end = tw.get("end")
    reason_hint = meta.get("time_window_reason") or "parsed_none"
    if meta.get("time_window_sanity_overridden"):
        reason_hint = "sanity_override"

    if start or end:
        if start and not end:
            try:
                end = _iso(_parse_iso(start) + timedelta(hours=2))
                reason = "triage_time_window_inferred_end"
            except Exception:
                reason = "triage_time_window"
        elif end and not start:
            try:
                start = _iso(_parse_iso(end) - timedelta(hours=2))
                reason = "triage_time_window_inferred_start"
            except Exception:
                reason = "triage_time_window"
        else:
            reason = "triage_time_window"
        return {
            "start": start,
            "end": end,
            "reason": reason_hint if reason_hint != "parsed_none" else reason,
            "source": meta.get("time_window_source") or "triage",
            "anchor": anchor_iso,
        }

    start = (anchor_dt - timedelta(hours=24)).isoformat().replace("+00:00", "Z")
    end = anchor_dt.isoformat().replace("+00:00", "Z")
    return {
        "start": start,
        "end": end,
        "reason": "fallback_last24h",
        "source": meta.get("time_window_source") or "triage",
        "anchor": anchor_iso,
    }


def _customer_time_window(triage_result: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, Any]:
    tw = triage_result.get("time_window") or {}
    return {
        "start": tw.get("start"),
        "end": tw.get("end"),
        "confidence": tw.get("confidence"),
        "reason": triage_result.get("time_window_reason") or meta.get("time_window_reason") or "none",
        "anchor": meta.get("time_window_anchor"),
    }


def _select_tools(triage_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    allowed = _allowed_tools(triage_result)
    suggested: List[Dict[str, Any]] = []
    for suggestion in triage_result.get("suggested_tools") or []:
        name = suggestion.get("tool_name")
        params = suggestion.get("params") if isinstance(suggestion, dict) else {}
        if name in registry.REGISTRY and (not allowed or name in allowed):
            suggested.append({"name": name, "params": params})

    if _should_run_log_tool(triage_result) and ("log_evidence" in allowed or not allowed):
        existing = {tool["name"] for tool in suggested}
        if "log_evidence" not in existing:
            lower_symptoms = " ".join([str(s) for s in triage_result.get("symptoms") or []]).lower()
            draft_body = (triage_result.get("draft_customer_reply") or {}).get("body", "").lower()
            haystack = f"{lower_symptoms} {draft_body}"
            if "timeout" in haystack:
                query_type = "timeouts"
            elif any(k in haystack for k in ["down", "unavailable", "outage"]):
                query_type = "availability"
            else:
                query_type = "errors"
            suggested.append(
                {
                    "name": "log_evidence",
                    "params": {
                        "service": "api",
                        "query_type": query_type,
                    },
                }
            )

    if suggested:
        return suggested

    # Fallback: legacy heuristic mapping to avoid zero-evidence runs.
    case_type = triage_result.get("case_type", "")
    recipient_domains = triage_result.get("scope", {}).get("recipient_domains") or []
    primary_domain = recipient_domains[0] if recipient_domains else None
    fallback: List[Dict[str, Any]] = []
    if case_type == "email_delivery" and ("fetch_email_events_sample" in allowed or not allowed):
        fallback.append({"name": "fetch_email_events_sample", "params": {"recipient_domain": primary_domain}})
        if primary_domain and ("dns_email_auth_check_sample" in allowed or not allowed):
            fallback.append({"name": "dns_email_auth_check_sample", "params": {"domain": primary_domain}})
    elif case_type == "integration" and (not allowed or "fetch_integration_events_sample" in allowed):
        fallback.append({"name": "fetch_integration_events_sample", "params": {"integration_name": "ats"}})
    elif case_type in {"ui_bug", "auth_access"} and (not allowed or "fetch_app_events_sample" in allowed):
        fallback.append({"name": "fetch_app_events_sample", "params": {}})
    return fallback


def _count_summary(field: str, bundles: List[Dict[str, Any]]) -> int:
    total = 0
    for bundle in bundles:
        counts = bundle.get("summary_counts") or {}
        total += int(counts.get(field) or 0)
    return total


def _load_truth_table() -> Dict[str, str]:
    try:
        return knowledge.load_knowledge()
    except Exception:
        return {}


def _guard_draft_claims(triage_result: Dict[str, Any], evidence_bundles: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Ensure the customer draft avoids unsupported factual claims."""
    draft = dict(triage_result.get("draft_customer_reply") or {"subject": "", "body": ""})
    draft_text = f"{draft.get('subject','')} {draft.get('body','')}".lower()
    if not draft_text.strip():
        return {"draft": draft, "warnings": []}

    evidence_text = json.dumps(evidence_bundles, ensure_ascii=False).lower()
    truth_table = _load_truth_table()
    truth_text = " ".join(truth_table.values()).lower()
    checks = {
        "bounce": lambda: ("bounce" in evidence_text) or _count_summary("bounced", evidence_bundles) > 0 or "bounce" in truth_text,
        "quarantine": lambda: "quarantine" in evidence_text or "quarantine" in truth_text,
        "dmarc": lambda: "dmarc" in evidence_text or "dmarc" in truth_text,
        "spf": lambda: "spf" in evidence_text or "spf" in truth_text,
        "rate limit": lambda: "rate limit" in evidence_text or "429" in evidence_text or "rate limit" in truth_text,
        "auth failed": lambda: "auth_failed" in evidence_text or "token expired" in evidence_text or "auth failed" in truth_text,
        "workflow disabled": lambda: "workflow_disabled" in evidence_text or "workflow disabled" in truth_text,
    }

    unsupported = []
    for keyword, predicate in checks.items():
        if keyword in draft_text and not predicate():
            unsupported.append(keyword)

    warnings: List[str] = []
    if unsupported:
        warnings.append(f"unsupported_claims:{','.join(sorted(set(unsupported)))}")
        questions = triage_result.get("missing_info_questions") or []
        safe_body_lines = [
            "Thanks for reaching out. We are still gathering evidence before confirming the exact cause.",
        ]
        if questions:
            safe_body_lines.append("To help us move faster, could you share:")
            safe_body_lines.extend(f"- {q}" for q in questions[:6])
        else:
            safe_body_lines.append("We'll follow up with more details once we finish collecting evidence.")
        fallback_subject = draft.get("subject") or "Quick update on your report"
        draft = {
            "subject": fallback_subject,
            "body": "\n".join(safe_body_lines),
        }
    return {"draft": draft, "warnings": warnings}


def _partition_evidence(bundles: List[Dict[str, Any]], allowed_tools: set[str]) -> Dict[str, List[Dict[str, Any]]]:
    relevant: List[Dict[str, Any]] = []
    other: List[Dict[str, Any]] = []
    for bundle in bundles:
        tool_name = (bundle.get("metadata") or {}).get("tool_name") or bundle.get("source")
        if allowed_tools and tool_name not in allowed_tools:
            other.append(bundle)
        else:
            relevant.append(bundle)
    return {"relevant": relevant, "other": other}


def _append_log_statement(draft: Dict[str, str], evidence_bundles: List[Dict[str, Any]], identity_confidence: str = "unknown", customer_tw: Dict[str, Any] | None = None) -> Dict[str, str]:
    log_bundle = next((b for b in evidence_bundles if (b.get("evidence_type") == "logs" or b.get("source") == "logs")), None)
    if not log_bundle:
        return draft
    query_type = (log_bundle.get("metadata") or {}).get("query_type") or "errors"
    observed = bool(log_bundle.get("observed_incident"))
    window = log_bundle.get("incident_window") or log_bundle.get("time_window") or {}
    start = window.get("start") or ""
    end = window.get("end") or ""
    summary_external = (log_bundle.get("metadata") or {}).get("summary_external")
    customer_line = ""
    if customer_tw and (customer_tw.get("start") or customer_tw.get("end")):
        cstart = customer_tw.get("start") or ""
        cend = customer_tw.get("end") or ""
        if cstart and cend:
            customer_line = f"Customer reports issues between {cstart} and {cend}."
        elif cstart:
            customer_line = f"Customer reports issues since {cstart}."
    lines = []
    if customer_line:
        lines.append(customer_line)
    prefix = "For your tenant, " if identity_confidence == "high" else "We checked our service signals and "
    summary = summary_external or (
        f"{prefix}observed elevated {query_type} signals between {start} and {end}; we're investigating."
        if observed
        else f"{prefix}did not observe {query_type} anomalies in the checked window."
    )
    if identity_confidence != "high":
        summary = f"{summary} Please confirm your organization/account ID and the affected time window."
    lines.append(summary)
    body = draft.get("body") or ""
    addition = "\n".join(lines).strip()
    if addition and addition not in body:
        body = f"{body}\n\n{addition}".strip()
    return {"subject": draft.get("subject", ""), "body": body}


def _append_service_status_statement(draft: Dict[str, str], evidence_bundles: List[Dict[str, Any]], identity_confidence: str = "unknown") -> Dict[str, str]:
    svc_bundle = next((b for b in evidence_bundles if (b.get("evidence_type") == "service_status")), None)
    if not svc_bundle:
        return draft
    summary = (svc_bundle.get("metadata") or {}).get("summary_external")
    if not summary:
        status = (svc_bundle.get("metadata") or {}).get("status") or "unknown"
        summary = f"Service status signal observed: {status}"
    if identity_confidence != "high":
        summary = f"{summary} Please confirm your organization/account ID and the affected time window."
    body = draft.get("body") or ""
    if summary not in body:
        body = f"{body}\n\nService check: {summary}".strip()
    return {"subject": draft.get("subject", ""), "body": body}


def process_once(processor_id: str) -> bool:
    row = queue_db.claim_row(processor_id)
    if not row:
        return False

    started_at = row.get("started_at") or _now_iso()
    text = _extract_text(row)
    conversation_id = row.get("conversation_id") or str(uuid4())
    retry_count = int(row.get("retry_count") or 0)

    metadata = {
        "tenant": row.get("end_user_handle") or row.get("customer") or "",
        "ingest_signature": row.get("ingest_signature") or "",
        "case_id": row.get("case_id"),
        "created_at": row.get("created_at"),
    }
    raw_payload = row.get("raw_payload")
    received_at = row.get("created_at")
    if raw_payload:
        try:
            raw_obj = json.loads(raw_payload)
            if isinstance(raw_obj, dict) and raw_obj.get("received_at"):
                received_at = raw_obj.get("received_at")
                metadata["received_at"] = received_at
        except Exception:
            pass

    idem = row.get("idempotency_key")
    if not idem:
        raw_bucket = row.get("created_at", "")[:10]
        key_input = f"{metadata.get('tenant','')}-{text[:200]}-{raw_bucket}"
        idem = hashlib.sha256(key_input.encode("utf-8")).hexdigest()
        queue_db.update_row_status(row["id"], status=row.get("status", "processing"), idempotency_key=idem)

    start = time.perf_counter()
    try:
        intake_id = queue_db.insert_intake(
            received_at=received_at or row.get("created_at") or _now_iso(),
            channel=row.get("channel") or "email",
            from_address=row.get("end_user_handle") or "",
            claimed_domain=None,
            subject_raw=row.get("payload_subject") or "",
            body_raw=text,
            attachments_json="[]",
            customer_request_id=json.dumps(_extract_request_ids(text).get("customer_request_ids") or []),
            error_code=",".join(_extract_request_ids(text).get("error_codes") or []),
        )

        resolved = queue_db.resolve_tenant({"from_domain": queue_db._domain_from_email(row.get("end_user_handle") or ""), "claimed_domain": None})
        identity_confidence = resolved.get("confidence", "unknown")
        queue_db.update_intake_tenant(intake_id, resolved.get("tenant_id"), identity_confidence)
        tenant_id = resolved.get("tenant_id") if identity_confidence == "high" else None
        metadata["tenant"] = tenant_id or metadata.get("tenant")

        service_ids = resolved.get("entitled_services") if identity_confidence == "high" else ["api"]

        triage_result = triage(text, metadata=metadata)
        elapsed = time.perf_counter() - start
        meta = triage_result.pop("_meta", {})
        query_tw = _derive_query_time_window(triage_result, meta)
        customer_tw = _customer_time_window(triage_result, meta)
        triage_result["customer_time_window"] = customer_tw
        triage_result["investigation_time_window"] = {"start": query_tw.get("start"), "end": query_tw.get("end")}
        allowed_tools = _allowed_tools(triage_result)

        evidence_bundles = []
        evidence_sources_run = []
        evidence_refs = []
        for tool in _select_tools(triage_result):
            try:
                params = tool.get("params") or {}
                if tool["name"] == "log_evidence":
                    params = dict(params)  # avoid mutating triage suggestion
                    params["time_window"] = {
                        "start": query_tw.get("start"),
                        "end": query_tw.get("end"),
                    }
                    params.setdefault("tenant", metadata.get("tenant"))
                    params.setdefault("service", metadata.get("tenant") or "api")
                    params.pop("start", None)
                    params.pop("end", None)
                elif tool["name"].startswith("fetch_"):
                    params.setdefault("start", query_tw.get("start"))
                    params.setdefault("end", query_tw.get("end"))
                evidence_record, bundle = evidence_runner.run_tool_with_evidence(intake_id, tool["name"], params)
                if "metadata" not in bundle:
                    bundle["metadata"] = {}
                bundle["metadata"]["query_time_window_reason"] = query_tw.get("reason")
                bundle["metadata"]["time_window_anchor"] = query_tw.get("anchor")
                bundle["metadata"]["time_window_source"] = query_tw.get("source")
                bundle["metadata"]["investigation_time_window"] = {"start": query_tw.get("start"), "end": query_tw.get("end")}
                bundle["metadata"]["customer_time_window"] = {"start": customer_tw.get("start"), "end": customer_tw.get("end")}
                bundle["metadata"]["customer_time_window_reason"] = customer_tw.get("reason")
                bundle["metadata"]["customer_time_window_anchor"] = customer_tw.get("anchor")
                bundle["metadata"]["customer_time_window_confidence"] = customer_tw.get("confidence")
                bundle["metadata"]["tool_name"] = tool["name"]
                evidence_bundles.append(bundle)
                evidence_sources_run.append(tool["name"])
                if evidence_record.get("evidence_id"):
                    evidence_refs.append({"evidence_id": evidence_record["evidence_id"], "tool": tool["name"], "params": params})
            except Exception as exc:
                evidence_sources_run.append(f"{tool['name']}:error:{exc}")

        if "service_status" in allowed_tools or not allowed_tools:
            for service_id in service_ids:
                try:
                    params = {"service_id": service_id, "tenant_id": tenant_id, "region": resolved.get("default_region")}
                    evidence_record, svc_bundle = evidence_runner.run_tool_with_evidence(intake_id, "service_status", params)
                    if "metadata" not in svc_bundle:
                        svc_bundle["metadata"] = {}
                    svc_bundle["metadata"]["query_time_window_reason"] = query_tw.get("reason")
                    svc_bundle["metadata"]["time_window_anchor"] = query_tw.get("anchor")
                    svc_bundle["metadata"]["time_window_source"] = query_tw.get("source")
                    svc_bundle["metadata"]["investigation_time_window"] = {"start": query_tw.get("start"), "end": query_tw.get("end")}
                    svc_bundle["metadata"]["customer_time_window"] = {"start": customer_tw.get("start"), "end": customer_tw.get("end")}
                    svc_bundle["metadata"]["customer_time_window_reason"] = customer_tw.get("reason")
                    svc_bundle["metadata"]["customer_time_window_anchor"] = customer_tw.get("anchor")
                    svc_bundle["metadata"]["customer_time_window_confidence"] = customer_tw.get("confidence")
                    svc_bundle["metadata"]["tool_name"] = "service_status"
                    evidence_bundles.append(svc_bundle)
                    evidence_sources_run.append("service_status")
                    if evidence_record.get("evidence_id"):
                        evidence_refs.append({"evidence_id": evidence_record["evidence_id"], "tool": "service_status", "service_id": service_id})
                except Exception as exc:
                    evidence_sources_run.append(f"service_status:error:{exc}")

        partitions = _partition_evidence(evidence_bundles, allowed_tools)
        relevant_bundles = partitions["relevant"]

        draft_guard = _guard_draft_claims(triage_result, relevant_bundles)
        triage_result["draft_customer_reply"] = draft_guard["draft"]
        triage_result["draft_customer_reply"] = _append_log_statement(triage_result["draft_customer_reply"], relevant_bundles, identity_confidence, customer_tw)
        triage_result["draft_customer_reply"] = _append_service_status_statement(triage_result["draft_customer_reply"], relevant_bundles, identity_confidence)

        final_report = report_service.generate_report(triage_result, relevant_bundles)
        report_meta = final_report.pop("_meta", {})
        handoff_payload = {
            "export_version": 1,
            "intake_id": intake_id,
            "tenant_id": tenant_id,
            "identity_confidence": resolved.get("confidence"),
            "symptoms": triage_result.get("symptoms") or [],
            "service_ids": service_ids,
            "time_window": query_tw,
            "evidence_refs": [ref for ref in evidence_refs if ref.get("tool") in allowed_tools or not allowed_tools],
            "recommended_next_steps": [
                "Replay log_evidence with same params",
                "Replay service_status checks for entitled services",
            ],
        }
        handoff_id = queue_db.create_handoff_pack(intake_id=intake_id, tier=3, payload_json=handoff_payload)
        queue_db.update_row_status(
            row["id"],
            status="triaged",
            conversation_id=conversation_id,
            payload=text,
            redacted_payload=meta.get("redacted_text") or text,
            processor_id=processor_id,
            started_at=started_at,
            finished_at=_now_iso(),
            latency_seconds=elapsed,
            triage_json=triage_result,
            draft_customer_reply_subject=triage_result["draft_customer_reply"]["subject"],
            draft_customer_reply_body=triage_result["draft_customer_reply"]["body"],
            triage_draft_subject=triage_result["draft_customer_reply"]["subject"],
            triage_draft_body=triage_result["draft_customer_reply"]["body"],
            missing_info_questions=triage_result.get("missing_info_questions") or [],
            llm_model=meta.get("llm_model", ""),
            prompt_version=meta.get("prompt_version", ""),
            redaction_applied=1 if meta.get("redaction_applied") else 0,
            triage_mode=meta.get("triage_mode", ""),
            llm_latency_ms=meta.get("llm_latency_ms"),
            llm_attempts=meta.get("llm_attempts"),
            schema_valid=1 if meta.get("schema_valid") else 0,
            evidence_json=evidence_bundles,
            evidence_sources_run=evidence_sources_run,
            evidence_created_at=_now_iso(),
            final_report_json=final_report,
            response_metadata={
                "triage_meta": meta,
                "report_meta": report_meta,
                "draft_warnings": draft_guard.get("warnings"),
                "handoff_id": handoff_id,
                "evidence_partition": {"relevant_count": len(relevant_bundles), "other_count": len(partitions["other"])},
            },
            case_id=meta.get("case_id") or row.get("case_id") or row.get("conversation_id"),
        )
        print(f"Processed triage for case={meta.get('case_id') or row.get('case_id') or row['id']} status=triaged latency={elapsed:.3f}s")
        metrics.incr("triage_success")
        metrics.timing("triage_latency_s", elapsed)
    except SchemaValidationError as exc:
        elapsed = time.perf_counter() - start
        queue_db.update_row_status(
            row["id"],
            status="dead_letter",
            processor_id=processor_id,
            started_at=started_at,
            finished_at=_now_iso(),
            latency_seconds=elapsed,
            retry_count=retry_count + 1,
            response_metadata={"error": str(exc), "dead_letter_reason": "schema_validation"},
        )
        print(f"Schema validation failed for case={row.get('case_id') or row['id']}: {exc}")
        metrics.incr("triage_failed_schema")
        metrics.incr("triage_dead_letter")
    except Exception as exc:  # pragma: no cover - defensive
        elapsed = time.perf_counter() - start
        next_retry = retry_count + 1
        if next_retry > config.MAX_RETRIES:
            queue_db.update_row_status(
                row["id"],
                status="dead_letter",
                processor_id=processor_id,
                started_at=started_at,
                finished_at=_now_iso(),
                latency_seconds=elapsed,
                retry_count=next_retry,
                response_metadata={"error": str(exc), "dead_letter_reason": "max_retries"},
            )
            print(f"Failed triage for case={row.get('case_id') or row['id']}: {exc} (dead-lettered)")
            metrics.incr("triage_dead_letter")
        else:
            delay = _backoff_seconds(retry_count)
            available_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
            queue_db.update_row_status(
                row["id"],
                status="queued",
                processor_id="",
                started_at=None,
                finished_at=None,
                latency_seconds=elapsed,
                retry_count=next_retry,
                available_at=available_at.isoformat().replace("+00:00", "Z"),
                response_metadata={
                    "error": str(exc),
                    "next_action": "retry",
                    "retry_in_seconds": delay,
                },
            )
            print(
                f"Retrying case={row.get('case_id') or row['id']} after error: {exc} (retry {next_retry}/{config.MAX_RETRIES}, next in {delay}s)"
            )
            metrics.incr("triage_retry")
        metrics.incr("triage_failed")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Triage worker (SQLite queue)")
    parser.add_argument("--processor-id", default="triage-worker-1", help="Identifier for this worker")
    parser.add_argument("--watch", action="store_true", help="Keep polling for new queued items")
    parser.add_argument("--poll-interval", type=float, default=3.0, help="Seconds between polls when --watch is set")
    args = parser.parse_args()

    while True:
        processed = process_once(args.processor_id)
        if not processed:
            if args.watch:
                time.sleep(max(args.poll_interval, 0.25))
                continue
            print("Queue empty. Nothing to process.")
            break


if __name__ == "__main__":
    main()
