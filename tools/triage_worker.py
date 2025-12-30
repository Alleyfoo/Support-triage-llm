#!/usr/bin/env python3
"""SQLite-backed worker that performs triage instead of chatbot replies."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

from app import queue_db
from app.triage_service import triage


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
        queue_db.update_row_status(
            row["id"],
            status="triaged",
            conversation_id=conversation_id,
            payload=text,
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
            response_metadata={"triage_meta": meta},
        )
        print(f"Processed triage for row {row['id']} status=triaged latency={elapsed:.3f}s")
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
