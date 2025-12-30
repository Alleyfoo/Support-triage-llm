#!/usr/bin/env python3
"""Ingest raw .eml files into the triage queue."""

from __future__ import annotations

import argparse
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Dict, List

from app import queue_db


def parse_eml(path: Path) -> Dict[str, str]:
    with path.open("rb") as f:
        msg = BytesParser(policy=policy.default).parse(f)
    subject = msg.get("subject", "")
    sender = msg.get("from", "")
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                body = part.get_content()
                break
    else:
        body = msg.get_content()
    return {
        "conversation_id": path.stem,
        "text": f"{subject}\n{body}".strip(),
        "end_user_handle": sender,
        "channel": "email",
        "ingest_signature": "eml-import",
        "raw_payload": msg.as_string(),
    }


def enqueue(messages: List[Dict[str, str]]) -> int:
    count = 0
    for msg in messages:
        _, created = queue_db.insert_message(
            {
                "conversation_id": msg.get("conversation_id") or "email",
                "text": msg.get("text", ""),
                "end_user_handle": msg.get("end_user_handle") or "",
                "channel": msg.get("channel") or "email",
                "raw_payload": msg.get("raw_payload") or "",
                "ingest_signature": msg.get("ingest_signature") or "",
            }
        )
        if created:
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest .eml files into triage queue")
    parser.add_argument("paths", nargs="+", help="Paths to .eml files or directories")
    args = parser.parse_args()

    files: List[Path] = []
    for p in args.paths:
        path = Path(p)
        if path.is_dir():
            files.extend(sorted(path.glob("*.eml")))
        elif path.is_file():
            files.append(path)
    messages = [parse_eml(p) for p in files]
    enq = enqueue(messages)
    print(f"Enqueued {enq} messages from {len(files)} eml files")


if __name__ == "__main__":
    main()
