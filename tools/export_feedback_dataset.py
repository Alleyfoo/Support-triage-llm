#!/usr/bin/env python3
"""
Export a redacted feedback dataset (human-edited replies + actions).

Gated: require LEARNING_MODE=dataset or --enable-dataset-export flag.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable

from app import queue_db

EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})", re.IGNORECASE)


def _parse_json(value: Any) -> Any:
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _contains_unredacted_email(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        for local, _ in EMAIL_RE.findall(value):
            redacted_local = local.lower()
            if "redacted" in redacted_local:
                continue
            return True
        return False
    if isinstance(value, dict):
        return any(_contains_unredacted_email(v) for v in value.values())
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        return any(_contains_unredacted_email(v) for v in value)
    return False


def _has_forbidden_keys(obj: Any) -> bool:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in {"raw_payload", "raw_text"}:
                return True
            if _has_forbidden_keys(v):
                return True
    elif isinstance(obj, Iterable) and not isinstance(obj, (str, bytes)):
        return any(_has_forbidden_keys(v) for v in obj)
    return False


def export_dataset(db_path: Path, out_path: Path, *, allow_dataset_export: bool = False) -> int:
    if not allow_dataset_export and os.environ.get("LEARNING_MODE", "").lower() != "dataset":
        print("Dataset export disabled. Set LEARNING_MODE=dataset or pass --enable-dataset-export.", file=sys.stderr)
        return 1

    # Ensure the queue_db module points at the requested DB path.
    queue_db.DB_PATH = db_path
    queue_db.init_db()

    rows = queue_db.fetch_queue(limit=1000)
    if not rows:
        print("No rows available to export.")
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with out_path.open("w", encoding="utf-8") as f:
        for row in rows:
            triage = _parse_json(row.get("triage_json")) or {}
            evidence = _parse_json(row.get("evidence_json")) or []
            report = _parse_json(row.get("final_report_json")) or {}
            error_tags = _parse_json(row.get("error_tags")) or []
            if isinstance(error_tags, str):
                error_tags = [error_tags]

            record: Dict[str, Any] = {
                "case_id": row.get("case_id") or row.get("conversation_id") or row.get("id"),
                "tenant": row.get("end_user_handle"),
                "triage_mode": row.get("triage_mode") or (triage.get("_meta") or {}).get("triage_mode"),
                "input_redacted": row.get("redacted_payload") or row.get("payload") or "",
                "triage": triage,
                "evidence": evidence,
                "report": report,
                "human_action": row.get("review_action"),
                "error_tags": error_tags,
                "human_final_reply": {
                    "subject": row.get("review_final_subject") or row.get("draft_customer_reply_subject") or "",
                    "body": row.get("review_final_body") or row.get("draft_customer_reply_body") or "",
                },
            }

            if _has_forbidden_keys(record):
                raise RuntimeError("Export aborted: found forbidden keys (raw_payload/raw_text) in record.")
            if _contains_unredacted_email(record):
                raise RuntimeError("Export aborted: detected unredacted email address in export payload.")

            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1

    print(f"Wrote {written} records to {out_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Export redacted feedback dataset (requires LEARNING_MODE=dataset).")
    parser.add_argument("--db-path", default=str(queue_db.DB_PATH), help="SQLite DB path")
    parser.add_argument("--out", default="data/learning/export_feedback.jsonl", help="Output JSONL path")
    parser.add_argument("--enable-dataset-export", action="store_true", help="Override LEARNING_MODE gate.")
    args = parser.parse_args()
    return export_dataset(Path(args.db_path), Path(args.out), allow_dataset_export=args.enable_dataset_export)


if __name__ == "__main__":
    raise SystemExit(main())
