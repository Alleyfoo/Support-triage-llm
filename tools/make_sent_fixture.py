#!/usr/bin/env python3
"""
Create a local .eml file for closed-loop simulation from a queue case.

Steps:
- Fetch a case by case_id.
- Take its draft body (or an override) and append the Internal Ref footer.
- Write a simple .eml with dummy headers for use with watch_sent_local.py.
"""

from __future__ import annotations

import argparse
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

from app import queue_db
from app.feedback_utils import append_footer


def _load_case(case_id: str) -> Optional[dict]:
    queue_db.init_db()
    conn = queue_db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM queue WHERE case_id = ? LIMIT 1", (case_id,))
        row = cursor.fetchone()
        return {key: row[key] for key in row.keys()} if row else None
    finally:
        conn.close()


def make_fixture(case_id: str, out_path: Path, override_body: Optional[str]) -> None:
    record = _load_case(case_id)
    if not record:
        raise SystemExit(f"Case not found: {case_id}")

    body = override_body if override_body is not None else (record.get("draft_customer_reply_body") or "")
    footerized = append_footer(body, case_id)

    msg = EmailMessage()
    msg["From"] = "agent@example.com"
    msg["To"] = record.get("end_user_handle") or "customer@example.com"
    msg["Subject"] = record.get("draft_customer_reply_subject") or "Support update"
    msg.set_content(footerized)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(msg.as_bytes())
    print(f"Wrote fixture to {out_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a local .eml for closed-loop simulation.")
    parser.add_argument("--case-id", required=True, help="Case ID to export")
    parser.add_argument("--out", default="test_sent_email.eml", help="Output .eml path")
    parser.add_argument("--body-file", help="Optional text file to use as the (pre-footer) body override")
    parser.add_argument("--body-text", help="Optional raw text to use as the (pre-footer) body override")
    args = parser.parse_args()

    override_body = None
    if args.body_text is not None:
        override_body = args.body_text
    elif args.body_file:
        override_body = Path(args.body_file).read_text(encoding="utf-8")

    make_fixture(args.case_id, Path(args.out), override_body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
