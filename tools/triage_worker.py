#!/usr/bin/env python3
"""SQLite-backed worker that performs triage instead of chatbot replies."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

from app import queue_db
from app.triage_service import triage
from app.validation import SchemaValidationError
from app import report_service, config
from tools import registry


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


def _select_tools(triage_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    mode = config.TOOL_SELECT_MODE
    tools: List[Dict[str, Any]] = []
    case_type = triage_result.get("case_type", "")
    recipient_domains = triage_result.get("scope", {}).get("recipient_domains") or []
    primary_domain = recipient_domains[0] if recipient_domains else None

    if mode in {"llm", "hybrid"}:
        for suggestion in triage_result.get("suggested_tools") or []:
            name = suggestion.get("tool_name")
            params = suggestion.get("params") if isinstance(suggestion, dict) else {}
            if name in registry.REGISTRY:
                tools.append({"name": name, "params": params})
        if mode == "llm" and tools:
            return tools
        if mode == "llm" and not tools:
            mode = "rules"

    if mode in {"rules", "hybrid"}:
        if case_type == "email_delivery":
            tools.append({"name": "fetch_email_events_sample", "params": {"recipient_domain": primary_domain}})
            if primary_domain:
                tools.append({"name": "dns_email_auth_check_sample", "params": {"domain": primary_domain}})
        elif case_type == "integration":
            tools.append({"name": "fetch_integration_events_sample", "params": {"integration_name": "ats"}})
        elif case_type == "ui_bug":
            tools.append({"name": "fetch_app_events_sample", "params": {}})
    return tools


def process_once(processor_id: str) -> bool:
    row = queue_db.claim_row(processor_id)
    if not row:
        return False

    started_at = row.get("started_at") or _now_iso()
    text = _extract_text(row)
    conversation_id = row.get("conversation_id") or str(uuid4())

    metadata = {
        "tenant": row.get("end_user_handle") or row.get("customer") or "",
        "ingest_signature": row.get("ingest_signature") or "",
    }

    start = time.perf_counter()
    try:
        triage_result = triage(text, metadata=metadata)
        elapsed = time.perf_counter() - start
        meta = triage_result.pop("_meta", {})

        evidence_bundles = []
        evidence_sources_run = []
        for tool in _select_tools(triage_result):
            try:
                bundle = registry.run_tool(tool["name"], tool.get("params"))
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
        )
        print(f"Processed triage for row {row['id']} status=triaged latency={elapsed:.3f}s")
    except SchemaValidationError as exc:
        elapsed = time.perf_counter() - start
        queue_db.update_row_status(
            row["id"],
            status="failed_schema",
            processor_id=processor_id,
            started_at=started_at,
            finished_at=_now_iso(),
            latency_seconds=elapsed,
            response_metadata={"error": str(exc)},
        )
        print(f"Schema validation failed for row {row['id']}: {exc}")
    except Exception as exc:  # pragma: no cover - defensive
        elapsed = time.perf_counter() - start
        queue_db.update_row_status(
            row["id"],
            status="failed",
            processor_id=processor_id,
            started_at=started_at,
            finished_at=_now_iso(),
            latency_seconds=elapsed,
            response_metadata={"error": str(exc)},
        )
        print(f"Failed triage for row {row['id']}: {exc}")
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
