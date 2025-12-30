#!/usr/bin/env python3
"""Sync triaged drafts to IMAP Drafts with Internal Ref footer."""

from __future__ import annotations

import argparse
import imaplib
import os
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List

from app import queue_db
from app.feedback_utils import append_footer


def _imap_connect() -> tuple[imaplib.IMAP4_SSL, str, str]:
    host = os.environ.get("IMAP_HOST")
    username = os.environ.get("IMAP_USERNAME")
    password = os.environ.get("IMAP_PASSWORD")
    if not host or not username or not password:
        raise RuntimeError("IMAP_HOST, IMAP_USERNAME, and IMAP_PASSWORD are required.")
    return imaplib.IMAP4_SSL(host), username, password


def _select_folder(imap: imaplib.IMAP4_SSL, folder: str) -> None:
    status, _ = imap.select(folder, readonly=False)
    if status != "OK":
        print(f"Could not select folder '{folder}'. Available folders:")
        status, mailboxes = imap.list()
        if status == "OK":
            for mbox in mailboxes or []:
                print(mbox.decode("utf-8", errors="ignore"))
        raise RuntimeError(f"Failed to select IMAP folder: {folder}")


def _fetch_candidates(limit: int) -> List[Dict[str, Any]]:
    queue_db.init_db()
    conn = queue_db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM queue
            WHERE status = 'triaged' AND delivery_status = 'pending'
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cursor.fetchall()
        return [{key: row[key] for key in row.keys()} for row in rows]
    finally:
        conn.close()


def _build_message(row: Dict[str, Any], username: str) -> EmailMessage:
    raw_case_id = row.get("case_id") or row.get("conversation_id") or row.get("id") or "unknown"
    case_id = str(raw_case_id)
    body = row.get("draft_customer_reply_body") or ""
    subject = row.get("draft_customer_reply_subject") or "Support update"
    recipient = row.get("end_user_handle") or ""

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["To"] = recipient
    msg["From"] = os.environ.get("IMAP_FROM") or username
    msg.set_content(append_footer(body, case_id))
    return msg


def sync_drafts(limit: int) -> int:
    candidates = _fetch_candidates(limit)
    if not candidates:
        print("No drafts to sync.")
        return 0

    imap, username, password = _imap_connect()
    try:
        imap.login(username, password)
        folder = os.environ.get("IMAP_FOLDER_DRAFTS") or "Drafts"
        _select_folder(imap, folder)

        synced = 0
        for row in candidates:
            msg = _build_message(row, username)
            status, _ = imap.append(folder, b"\\Draft", None, msg.as_bytes())
            if status != "OK":
                print(f"Failed to append draft for case {row.get('case_id') or row.get('id')}")
                continue
            queue_db.update_row_status(
                row["id"],
                status="awaiting_human",
                delivery_status="draft_synced",
            )
            synced += 1
        print(f"Synced {synced} drafts to IMAP.")
        return synced
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync triaged drafts to IMAP Drafts.")
    parser.add_argument("--limit", type=int, default=50, help="Maximum drafts to sync per run")
    args = parser.parse_args()
    sync_drafts(args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
