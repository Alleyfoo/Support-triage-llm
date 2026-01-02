#!/usr/bin/env python3
"""IMAP → SQLite ingestion for the triage queue.

Polls an IMAP mailbox, parses messages, and enqueues them into SQLite via queue_db.insert_message.
Idempotency: message_id column is unique and we also set an ingest_signature with IMAP UID.

Usage:
  python tools/imap_ingest_db.py --once
  python tools/imap_ingest_db.py --watch --poll-interval 30

Env:
  IMAP_HOST, IMAP_USERNAME, IMAP_PASSWORD (required)
  IMAP_FOLDER (default: INBOX)
  IMAP_PROCESSED_FOLDER (optional: move processed messages here)
"""

from __future__ import annotations

import argparse
import email
import imaplib
import os
import time
from email.header import decode_header, make_header
from email.message import Message
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app import queue_db


def _decode_header(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _extract_body(msg: Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            disp = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            if part.get_content_type() == "text/plain":
                try:
                    payload = part.get_payload(decode=True) or b""
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                except Exception:
                    continue
    payload = msg.get_payload(decode=True) or b""
    try:
        return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    except Exception:
        return payload.decode(errors="replace")


def _imap_connect() -> imaplib.IMAP4_SSL:
    host = os.environ.get("IMAP_HOST")
    username = os.environ.get("IMAP_USERNAME")
    password = os.environ.get("IMAP_PASSWORD")
    if not host or not username or not password:
        raise RuntimeError("IMAP_HOST, IMAP_USERNAME, and IMAP_PASSWORD are required.")
    client = imaplib.IMAP4_SSL(host)
    status, _ = client.login(username, password)
    if status != "OK":
        raise RuntimeError("IMAP login failed")
    return client


def _select_folder(imap: imaplib.IMAP4_SSL, folder: str) -> Tuple[str, Optional[str]]:
    status, data = imap.select(folder, readonly=False)
    if status != "OK":
        raise RuntimeError(f"Failed to select folder {folder}")
    uidvalidity = None
    if data:
        try:
            resp = imap.response("UIDVALIDITY")
            if resp and resp[1]:
                uidvalidity = resp[1][0].decode()
        except Exception:
            uidvalidity = None
    return status, uidvalidity


def _mark_processed(imap: imaplib.IMAP4_SSL, uid: str, *, processed_folder: Optional[str]) -> None:
    try:
        if processed_folder:
            # Copy then delete from source
            imap.uid("COPY", uid, processed_folder)
            imap.uid("STORE", uid, "+FLAGS", r"(\Deleted)")
        else:
            imap.uid("STORE", uid, "+FLAGS", r"(\Seen)")
    except Exception:
        return


def _parse_message(raw_bytes: bytes, uid: str, uidvalidity: Optional[str], folder: str) -> Dict[str, str]:
    msg = email.message_from_bytes(raw_bytes)
    subject = _decode_header(msg.get("Subject"))
    sender = _decode_header(msg.get("From"))
    message_id = (msg.get("Message-ID") or "").strip()
    body = _extract_body(msg)
    raw_payload = raw_bytes.decode(errors="replace")
    references = msg.get_all("References", [])
    in_reply_to = msg.get("In-Reply-To", "")
    ref_header = " ".join(references) if references else ""

    source_key = f"{folder}:{uidvalidity or 'novalid'}:{uid}"
    idempotency = source_key

    return {
        "text": body,
        "end_user_handle": sender,
        "payload_subject": subject,
        "raw_payload": raw_payload,
        "message_id": message_id or f"imap-uid-{uid}",
        "ingest_signature": f"imap:{source_key}",
        "source_message_key": source_key,
        "idempotency_key": idempotency,
        "channel": "email",
        "conversation_id": in_reply_to or message_id or f"imap-uid-{uid}",
        "references": ref_header,
        "in_reply_to": in_reply_to,
    }


def _ingest_once(imap: imaplib.IMAP4_SSL, *, folder: str, processed_folder: Optional[str], limit: int) -> int:
    _, uidvalidity = _select_folder(imap, folder)
    status, data = imap.uid("SEARCH", None, "UNSEEN")
    if status != "OK":
        return 0
    uids = (data[0] or b"").split()
    if not uids:
        return 0
    count = 0
    for uid in uids[:limit]:
        status, fetched = imap.uid("FETCH", uid, "(RFC822)")
        if status != "OK" or not fetched or not isinstance(fetched[0], tuple):
            continue
        raw_bytes = fetched[0][1]
        payload = _parse_message(raw_bytes, uid.decode(), uidvalidity, folder)
        row_id, created = queue_db.insert_message(payload)
        if created:
            _mark_processed(imap, uid.decode(), processed_folder=processed_folder)
            count += 1
        else:
            # already ingested; still mark as seen to avoid loops
            _mark_processed(imap, uid.decode(), processed_folder=processed_folder)
    if processed_folder:
        try:
            imap.expunge()
        except Exception:
            pass
    return count


def ingest_from_env(*, limit: int = 50, folder: Optional[str] = None, processed_folder: Optional[str] = None) -> int:
    """One-shot IMAP ingest using environment configuration.

    Returns the number of new messages enqueued. If IMAP is not configured, returns 0.
    """
    host = os.environ.get("IMAP_HOST")
    username = os.environ.get("IMAP_USERNAME")
    password = os.environ.get("IMAP_PASSWORD")
    if not host or not username or not password:
        return 0

    folder = folder or os.environ.get("IMAP_FOLDER") or "INBOX"
    processed_folder = processed_folder or os.environ.get("IMAP_PROCESSED_FOLDER")

    imap = _imap_connect()
    try:
        return _ingest_once(imap, folder=folder, processed_folder=processed_folder, limit=max(1, limit))
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest IMAP emails into the SQLite queue.")
    parser.add_argument("--folder", default=os.environ.get("IMAP_FOLDER") or "INBOX", help="IMAP folder to poll")
    parser.add_argument("--processed-folder", default=os.environ.get("IMAP_PROCESSED_FOLDER"), help="Move processed messages here (optional)")
    parser.add_argument("--watch", action="store_true", help="Keep polling")
    parser.add_argument("--poll-interval", type=float, default=30.0, help="Seconds between polls when watching")
    parser.add_argument("--limit", type=int, default=50, help="Max messages to process per poll")
    args = parser.parse_args()

    while True:
        try:
            imap = _imap_connect()
            try:
                processed = _ingest_once(imap, folder=args.folder, processed_folder=args.processed_folder, limit=max(1, args.limit))
                if processed:
                    print(f"Ingested {processed} message(s) into SQLite queue.")
                else:
                    print("No new messages.")
            finally:
                try:
                    imap.logout()
                except Exception:
                    pass
        except Exception as exc:
            print(f"[imap_ingest] error: {exc}")

        if not args.watch:
            break
        time.sleep(max(1.0, args.poll_interval))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
