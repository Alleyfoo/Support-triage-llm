#!/usr/bin/env python3
"""Sync triaged drafts to IMAP Drafts with Internal Ref footer."""

from __future__ import annotations

import argparse
import imaplib
import os
from email.message import EmailMessage
from email import message_from_string
import json
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


def _extract_thread_headers(row: Dict[str, Any]) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    raw = row.get("raw_payload") or ""
    msg = None
    try:
        msg = message_from_string(raw)
    except Exception:
        msg = None
    in_reply_to = ""
    references = ""
    if msg:
        in_reply_to = msg.get("Message-ID", "") or msg.get("In-Reply-To", "") or ""
        references = msg.get("References", "") or ""
    if not in_reply_to:
        in_reply_to = str(row.get("message_id") or "")
    if in_reply_to:
        headers["In-Reply-To"] = in_reply_to
    if references:
        headers["References"] = references
    return headers


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
    for header, value in _extract_thread_headers(row).items():
        if value:
            msg[header] = value
    return msg


def _build_internal_message(row: Dict[str, Any], username: str) -> EmailMessage:
    """Create an internal-only draft with analysis attached."""
    raw_case_id = row.get("case_id") or row.get("conversation_id") or row.get("id") or "unknown"
    case_id = str(raw_case_id)
    msg = EmailMessage()
    msg["Subject"] = f"[Internal] Case {case_id} analysis"
    msg["To"] = os.environ.get("IMAP_INTERNAL_TO") or username
    msg["From"] = os.environ.get("IMAP_FROM") or username
    msg.set_content(f"Internal analysis for case {case_id}. Do not send to customer.")

    bundle = {
        "case_id": case_id,
        "queue_row_id": row.get("id"),
        "triage_json": row.get("triage_json"),
        "evidence_json": row.get("evidence_json"),
        "final_report_json": row.get("final_report_json"),
        "response_metadata": row.get("response_metadata"),
        "created_at": row.get("created_at"),
        "conversation_id": row.get("conversation_id"),
        "message_id": row.get("message_id"),
    }
    payload = json.dumps(bundle, ensure_ascii=False, indent=2)
    payload_bytes = payload.encode("utf-8")
    if len(payload_bytes) > 500_000:
        summary = {
            "case_id": case_id,
            "note": "Analysis bundle too large to attach; see local storage for full evidence.",
        }
        payload_bytes = json.dumps(summary, ensure_ascii=False, indent=2).encode("utf-8")
    msg.add_attachment(
        payload_bytes,
        maintype="application",
        subtype="json",
        filename=f"case_{case_id}_analysis.json",
    )
    return msg


def sync_drafts(limit: int, *, force: bool = False) -> int:
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
            if row.get("draft_synced_at") and not force:
                continue
            msg = _build_message(row, username)
            status, _ = imap.append(folder, b"\\Draft", None, msg.as_bytes())
            if status != "OK":
                print(f"Failed to append draft for case {row.get('case_id') or row.get('id')}")
                continue

            internal_enabled = (os.environ.get("SYNC_INTERNAL_ANALYSIS") or "0").lower() in {"1", "true", "yes", "on"}
            if internal_enabled:
                internal_to = os.environ.get("IMAP_INTERNAL_TO")
                if not internal_to:
                    raise RuntimeError("SYNC_INTERNAL_ANALYSIS=1 but IMAP_INTERNAL_TO is not set.")
                internal_folder = os.environ.get("IMAP_FOLDER_INTERNAL") or folder
                try:
                    _select_folder(imap, internal_folder)
                    internal_msg = _build_internal_message(row, username)
                    internal_msg["To"] = internal_to
                    imap.append(internal_folder, b"\\Draft", None, internal_msg.as_bytes())
                    _select_folder(imap, folder)  # switch back
                except Exception as exc:
                    print(f"Internal draft failed for case {row.get('case_id')}: {exc}")

            queue_db.update_row_status(
                row["id"],
                status="awaiting_human",
                delivery_status="draft_synced",
                draft_synced_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                draft_message_id=msg.get("Message-ID", ""),
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
    parser.add_argument("--force", action="store_true", help="Re-sync drafts even if already synced")
    args = parser.parse_args()
    sync_drafts(args.limit, force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
