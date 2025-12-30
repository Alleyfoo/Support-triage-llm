#!/usr/bin/env python3
"""Curate golden dataset entries from closed-loop feedback rows."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

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
            if "redacted" in local.lower():
                continue
            return True
        return False
    if isinstance(value, dict):
        return any(_contains_unredacted_email(v) for v in value.values())
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        return any(_contains_unredacted_email(v) for v in value)
    return False


def _quality(edit_distance: float) -> str:
    if edit_distance <= 0.05:
        return "perfect"
    if edit_distance <= 0.60:
        return "correction"
    return "rejection"


def _fetch_closed(limit: int) -> List[Dict[str, Any]]:
    queue_db.init_db()
    conn = queue_db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM queue
            WHERE closed_loop_at IS NOT NULL
            ORDER BY closed_loop_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cursor.fetchall()
        return [{key: row[key] for key in row.keys()} for row in rows]
    finally:
        conn.close()


def curate(out_path: Path, *, limit: int, include_rejections: bool) -> Tuple[int, int, int]:
    rows = _fetch_closed(limit)
    if not rows:
        print("No closed-loop rows found.")
        return (0, 0, 0)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    perfect = correction = rejection = 0
    with out_path.open("w", encoding="utf-8") as f:
        for row in rows:
            try:
                edit_distance = float(row.get("edit_distance"))
            except (TypeError, ValueError):
                continue
            quality = _quality(edit_distance)
            if quality == "rejection" and not include_rejections:
                rejection += 1
                continue

            sent_body = row.get("sent_body") or ""
            input_symptoms = row.get("redacted_payload") or row.get("payload") or ""
            triage = _parse_json(row.get("triage_json")) or {}
            draft = {
                "subject": row.get("draft_customer_reply_subject") or row.get("review_final_subject") or "",
                "body": row.get("draft_customer_reply_body") or row.get("review_final_body") or "",
            }
            example = {
                "case_id": row.get("case_id") or row.get("conversation_id") or row.get("id"),
                "quality": quality,
                "edit_distance": edit_distance,
                "input_symptoms": input_symptoms,
                "triage": triage,
                "draft_customer_reply": draft,
                "sent_body": sent_body,
            }

            if _contains_unredacted_email(example):
                raise RuntimeError(f"Unredacted email detected in case {example['case_id']}; aborting.")

            if quality == "perfect":
                perfect += 1
            elif quality == "correction":
                correction += 1
            else:
                rejection += 1

            f.write(json.dumps(example, ensure_ascii=False) + "\n")

    print(f"Wrote dataset to {out_path} (perfect={perfect}, correction={correction}, rejection={rejection})")
    return (perfect, correction, rejection)


def main() -> int:
    parser = argparse.ArgumentParser(description="Curate golden dataset from closed-loop feedback.")
    parser.add_argument("--out", default="data/learning/golden_dataset.jsonl", help="Output JSONL path")
    parser.add_argument("--limit", type=int, default=5000, help="Max rows to scan")
    parser.add_argument("--include-rejections", action="store_true", help="Include rejection-class rows in output")
    args = parser.parse_args()
    curate(Path(args.out), limit=args.limit, include_rejections=args.include_rejections)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
