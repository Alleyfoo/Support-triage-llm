#!/usr/bin/env python3
"""SQLite-backed worker that performs triage instead of chatbot replies."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4
import hashlib

from app import queue_db
from app.triage_service import triage
from app.validation import SchemaValidationError
from app import report_service, config, metrics
from tools import registry

EXPECTED_TOOLS_BY_CASE = {
    # Legacy fallback when LLM does not suggest anything valid.
    "email_delivery": {"fetch_email_events_sample", "dns_email_auth_check_sample"},
    "integration": {"fetch_integration_events_sample"},
    "auth_access": {"fetch_app_events_sample"},
    "ui_bug": {"fetch_app_events_sample"},
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _extract_text(row: Dict[str, Any]) -> str:
    return str(
        row.get("payload")
        or row.get("raw_payload")
        or row.get("body")
        or row.get("text")
        or ""
    ).strip()


def _backoff_seconds(retry_count: int) -> int:
    base = max(config.RETRY_BASE_SECONDS, 1)
    max_wait = max(config.RETRY_MAX_SECONDS, base)
    return min(int(base * (2**retry_count)), max_wait)


def _derive_query_time_window(triage_result: Dict[str, Any]) -> Dict[str, Any]:
    tw = triage_result.get("time_window") or {}
    start = tw.get("start")
    end = tw.get("end")
    if start or end:
        return {"start": start, "end": end, "reason": "triage_time_window"}
    now = datetime.now(timezone.utc)
    return {
        "start": (now - timedelta(hours=24)).isoformat().replace("+00:00", "Z"),
        "end": now.isoformat().replace("+00:00", "Z"),
        "reason": "default_no_date",
    }


def _select_tools(triage_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    suggested: List[Dict[str, Any]] = []
    for suggestion in triage_result.get("suggested_tools") or []:
        name = suggestion.get("tool_name")
        params = suggestion.get("params") if isinstance(suggestion, dict) else {}
        if name in registry.REGISTRY:
            suggested.append({"name": name, "params": params})

    if suggested:
        return suggested

    # Fallback: legacy heuristic mapping to avoid zero-evidence runs.
    case_type = triage_result.get("case_type", "")
    recipient_domains = triage_result.get("scope", {}).get("recipient_domains") or []
    primary_domain = recipient_domains[0] if recipient_domains else None
    fallback: List[Dict[str, Any]] = []
    if case_type == "email_delivery":
        fallback.append({"name": "fetch_email_events_sample", "params": {"recipient_domain": primary_domain}})
        if primary_domain:
            fallback.append({"name": "dns_email_auth_check_sample", "params": {"domain": primary_domain}})
    elif case_type == "integration":
        fallback.append({"name": "fetch_integration_events_sample", "params": {"integration_name": "ats"}})
    elif case_type in {"ui_bug", "auth_access"}:
        fallback.append({"name": "fetch_app_events_sample", "params": {}})
    return fallback


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
    }

    idem = row.get("idempotency_key")
    if not idem:
        raw_bucket = row.get("created_at", "")[:10]
        key_input = f"{metadata.get('tenant','')}-{text[:200]}-{raw_bucket}"
        idem = hashlib.sha256(key_input.encode("utf-8")).hexdigest()
        queue_db.update_row_status(row["id"], status=row.get("status", "processing"), idempotency_key=idem)

    start = time.perf_counter()
    try:
        triage_result = triage(text, metadata=metadata)
        elapsed = time.perf_counter() - start
        meta = triage_result.pop("_meta", {})
        query_tw = _derive_query_time_window(triage_result)

        evidence_bundles = []
        evidence_sources_run = []
        for tool in _select_tools(triage_result):
            try:
                params = tool.get("params") or {}
                params.setdefault("start", query_tw.get("start"))
                params.setdefault("end", query_tw.get("end"))
                bundle = registry.run_tool(tool["name"], params)
                if "metadata" not in bundle:
                    bundle["metadata"] = {}
                bundle["metadata"]["query_time_window_reason"] = query_tw.get("reason")
                evidence_bundles.append(bundle)
                evidence_sources_run.append(tool["name"])
            except Exception as exc:
                evidence_sources_run.append(f"{tool['name']}:error:{exc}")

        final_report = report_service.generate_report(triage_result, evidence_bundles)
        report_meta = final_report.pop("_meta", {})
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
            response_metadata={"triage_meta": meta, "report_meta": report_meta},
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
