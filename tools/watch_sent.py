#!/usr/bin/env python3
"""Watch IMAP Sent folder, detect closed-loop replies, and record edit distance."""

from __future__ import annotations

import argparse
import imaplib
import os
import email
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.message import Message
from typing import Any, Dict, Optional

from app import queue_db, email_preprocess
from app.feedback_utils import extract_case_id, strip_footer, BODY_SIZE_CAP


def _imap_connect() -> tuple[imaplib.IMAP4_SSL, str, str]:
    host = os.environ.get("IMAP_HOST")
    username = os.environ.get("IMAP_USERNAME")
    password = os.environ.get("IMAP_PASSWORD")
    if not host or not username or not password:
        raise RuntimeError("IMAP_HOST, IMAP_USERNAME, and IMAP_PASSWORD are required.")
    return imaplib.IMAP4_SSL(host), username, password


def _select_folder(imap: imaplib.IMAP4_SSL, folder: str) -> None:
    status, _ = imap.select(folder, readonly=True)
    if status != "OK":
        print(f"Could not select folder '{folder}'. Available folders:")
        status, mailboxes = imap.list()
        if status == "OK":
            for mbox in mailboxes or []:
                print(mbox.decode("utf-8", errors="ignore"))
        raise RuntimeError(f"Failed to select IMAP folder: {folder}")


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


def _find_case(case_id: str) -> Optional[Dict[str, Any]]:
    queue_db.init_db()
    conn = queue_db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM queue WHERE case_id = ? LIMIT 1",
            (case_id,),
        )
        row = cursor.fetchone()
        return {key: row[key] for key in row.keys()} if row else None
    finally:
        conn.close()


def _compute_edit_distance(draft: str, sent: str) -> float:
    similarity = SequenceMatcher(None, draft or "", sent or "").ratio()
    return max(0.0, min(1.0, 1.0 - similarity))


def watch_sent(lookback_hours: int, limit: int, dry_run: bool) -> int:
    imap, username, password = _imap_connect()
    try:
        imap.login(username, password)
        folder = os.environ.get("IMAP_FOLDER_SENT") or "Sent"
        _select_folder(imap, folder)

        since_date = (datetime.now(timezone.utc) - timedelta(hours=max(lookback_hours, 1))).strftime("%d-%b-%Y")
        status, data = imap.search(None, "SINCE", since_date)
        if status != "OK":
            raise RuntimeError("IMAP search failed.")
        ids = data[0].split()
        if not ids:
            print("No sent messages found in window.")
            return 0
        processed = 0
        for msg_id in ids[-limit:]:
            status, payload = imap.fetch(msg_id, "(RFC822)")
            if status != "OK" or not payload:
                continue
            raw_email = payload[0][1]
            msg = email.message_from_bytes(raw_email)
            body = _extract_body(msg)
            case_id = extract_case_id(body)
            if not case_id:
                continue
            record = _find_case(case_id)
            if not record:
                continue
            if record.get("closed_loop_at"):
                continue
            sent_body = strip_footer(body)
            draft_body = (record.get("review_final_body") or record.get("draft_customer_reply_body") or "").strip()[:BODY_SIZE_CAP]
            distance = _compute_edit_distance(draft_body, sent_body)
            if dry_run:
                print(f"[DRY-RUN] Would close case {case_id} with edit_distance={distance:.3f}")
            else:
                queue_db.update_row_status(
                    record["id"],
                    status="responded",
                    sent_body=sent_body,
                    edit_distance=distance,
                    feedback_source="sent_folder_watch",
                    closed_loop_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                )
                print(f"Case {case_id} closed. Edit distance: {distance:.3f}")
            processed += 1
        if processed == 0:
            print("No matching sent messages processed.")
        return processed
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch IMAP Sent for closed-loop feedback.")
    parser.add_argument("--lookback-hours", type=int, default=int(os.environ.get("IMAP_SENT_LOOKBACK_HOURS") or 24))
    parser.add_argument("--limit", type=int, default=200, help="Maximum messages to inspect per run")
    parser.add_argument("--dry-run", action="store_true", help="Print intended updates without modifying the DB")
    args = parser.parse_args()
    watch_sent(args.lookback_hours, args.limit, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
