#!/usr/bin/env python3
"""Offline harness to parse .eml files, detect Internal Ref footer, and diff against drafts."""

from __future__ import annotations

import argparse
import email
from difflib import SequenceMatcher
from email.message import Message
from pathlib import Path
from typing import Any, Dict, Optional

from app import queue_db, email_preprocess
from app.feedback_utils import extract_case_id, strip_footer, BODY_SIZE_CAP


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="ignore")
    except LookupError:
        return payload.decode("utf-8", errors="ignore")


def _extract_body(msg: Message) -> str:
    """Prefer text/plain; fall back to text/html -> text; truncate to BODY_SIZE_CAP."""
    plain_text: Optional[str] = None
    html_text: Optional[str] = None

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                plain_text = _decode_part(part)
                break
            if ctype == "text/html" and html_text is None:
                html_text = _decode_part(part)
    else:
        ctype = msg.get_content_type()
        if ctype == "text/plain":
            plain_text = _decode_part(msg)
        elif ctype == "text/html":
            html_text = _decode_part(msg)

    text = plain_text or ""
    if not text and html_text:
        text = email_preprocess.html_to_text(html_text)
    text = (text or "").strip()
    if len(text) > BODY_SIZE_CAP:
        text = text[-BODY_SIZE_CAP:]
    return text


def _compute_edit_distance(draft: str, sent: str) -> float:
    similarity = SequenceMatcher(None, draft or "", sent or "").ratio()
    return max(0.0, min(1.0, 1.0 - similarity))


def _load_message(path: Path) -> Message:
    with path.open("rb") as f:
        return email.message_from_binary_file(f)


def _find_case(case_id: str) -> Optional[Dict[str, Any]]:
    queue_db.init_db()
    conn = queue_db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM queue WHERE case_id = ? LIMIT 1", (case_id,))
        row = cursor.fetchone()
        return {key: row[key] for key in row.keys()} if row else None
    finally:
        conn.close()


def run(paths: list[Path], draft_text: Optional[str], update_db: bool, use_db: bool) -> int:
    processed = 0
    for path in paths:
        msg = _load_message(path)
        body = _extract_body(msg)
        case_id = extract_case_id(body)
        if not case_id:
            print(f"{path}: no Internal Ref footer found (ignored)")
            continue

        record: Optional[Dict[str, Any]] = None
        draft_body = draft_text or ""
        if use_db:
            record = _find_case(case_id)
            if record:
                draft_body = (
                    record.get("review_final_body")
                    or record.get("draft_customer_reply_body")
                    or ""
                )

        if not draft_body:
            print(f"{path}: case={case_id} no draft body available; skipping diff")
            continue

        sent_body = strip_footer(body)
        distance = _compute_edit_distance(draft_body.strip()[:BODY_SIZE_CAP], sent_body)
        print(f"{path}: case={case_id} edit_distance={distance:.3f}")

        if update_db and record:
            queue_db.update_row_status(
                record["id"],
                status="responded",
                sent_body=sent_body,
                edit_distance=distance,
                feedback_source="sent_folder_watch",
                closed_loop_at=queue_db._now_iso(),
            )
            print(f"  -> DB updated for case {case_id}")
        processed += 1
    return processed


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline .eml parser for closed-loop feedback.")
    parser.add_argument("eml", nargs="+", help="Path(s) to .eml files to inspect")
    parser.add_argument("--draft-file", help="Path to a draft body file used for diffing when DB is not used")
    parser.add_argument("--draft-text", help="Draft body text used for diffing when DB is not used")
    parser.add_argument("--use-db", action="store_true", help="Fetch draft body from queue.db using case_id")
    parser.add_argument("--update-db", action="store_true", help="Write back sent_body/edit_distance to DB when use-db is set")
    args = parser.parse_args()

    draft_text = args.draft_text
    if args.draft_file:
        draft_text = Path(args.draft_file).read_text(encoding="utf-8")

    paths = [Path(p) for p in args.eml]
    run(paths, draft_text, update_db=args.update_db, use_db=args.use_db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
