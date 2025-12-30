#!/usr/bin/env python3
"""
Curate a high-quality "golden" dataset of reviewed tickets for few-shot learning.

Filters:
- review_action in {approved, escalate} (or rewrite only if finalized)
- diff_body_ratio > 0.05
- error_tags empty
Outputs a JSONL file with Learning Example schema:
  - input_symptoms
  - perfect_triage
  - perfect_reply
  - reasoning (optional)
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

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


def _is_high_quality(row: Dict[str, Any]) -> bool:
    action = (row.get("review_action") or "").lower()
    if action == "rewrite" and not (row.get("review_final_body") or row.get("review_final_subject")):
        return False
    if action not in {"approved", "escalate", "rewrite"}:
        return False

    try:
        diff_body_ratio = float(row.get("diff_body_ratio") or 0.0)
    except (TypeError, ValueError):
        diff_body_ratio = 0.0
    if diff_body_ratio <= 0.05:
        return False

    error_tags = _parse_json(row.get("error_tags")) or []
    if isinstance(error_tags, str):
        error_tags = [error_tags] if error_tags else []
    if error_tags:
        return False

    return True


def curate_dataset(db_path: Path, out_path: Path, *, limit: int = 5000) -> int:
    queue_db.DB_PATH = db_path
    queue_db.init_db()
    rows = queue_db.fetch_queue(limit=limit)
    if not rows:
        print("No rows available to curate.")
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with out_path.open("w", encoding="utf-8") as f:
        for row in rows:
            if not _is_high_quality(row):
                continue

            triage = _parse_json(row.get("triage_json")) or {}
            perfect_reply = {
                "subject": row.get("review_final_subject") or row.get("draft_customer_reply_subject") or "",
                "body": row.get("review_final_body") or row.get("draft_customer_reply_body") or "",
            }
            record = {
                "input_symptoms": row.get("redacted_payload") or row.get("payload") or "",
                "perfect_triage": triage,
                "perfect_reply": perfect_reply,
                "reasoning": row.get("review_notes") or "",
                "case_id": row.get("case_id") or row.get("conversation_id") or row.get("id"),
            }

            if _contains_unredacted_email(record):
                raise RuntimeError("Curate aborted: detected unredacted email address in payload.")

            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1

    print(f"Wrote {written} golden examples to {out_path}")
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Curate golden dataset of reviewed tickets.")
    parser.add_argument("--db-path", default=str(queue_db.DB_PATH), help="SQLite DB path (queue.db)")
    parser.add_argument(
        "--out",
        default="data/learning/golden_dataset.jsonl",
        help="Output JSONL path for curated golden examples",
    )
    parser.add_argument("--limit", type=int, default=5000, help="Max rows to scan from queue")
    args = parser.parse_args()
    curate_dataset(Path(args.db_path), Path(args.out), limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
