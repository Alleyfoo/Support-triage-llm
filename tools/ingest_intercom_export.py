#!/usr/bin/env python3
"""Ingest a simulated Intercom export into the triage queue."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List

from app import queue_db


def parse_export(path: Path) -> List[Dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    conversations = data if isinstance(data, list) else data.get("conversations", [])
    messages: List[Dict[str, str]] = []
    for convo in conversations:
        parts = convo.get("parts") or []
        body_chunks = []
        if convo.get("body"):
            body_chunks.append(convo["body"])
        body_chunks.extend(p.get("body", "") for p in parts if p.get("body"))
        body = "\n".join(chunk for chunk in body_chunks if chunk)
        messages.append(
            {
                "conversation_id": str(convo.get("id") or convo.get("conversation_id") or ""),
                "text": body,
                "end_user_handle": (convo.get("user") or {}).get("email") or (convo.get("contacts") or [{}])[0].get("email") or "",
                "channel": "intercom",
                "ingest_signature": "intercom-export",
                "raw_payload": json.dumps(convo, ensure_ascii=False),
            }
        )
    return messages


def enqueue(messages: List[Dict[str, str]]) -> int:
    count = 0
    for msg in messages:
        _, created = queue_db.insert_message(
            {
                "conversation_id": msg.get("conversation_id") or "intercom",
                "text": msg.get("text", ""),
                "end_user_handle": msg.get("end_user_handle") or "",
                "channel": msg.get("channel") or "intercom",
                "raw_payload": msg.get("raw_payload") or "",
                "ingest_signature": msg.get("ingest_signature") or "",
            }
        )
        if created:
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Intercom-like export JSON into triage queue")
    parser.add_argument("path", help="Path to Intercom export JSON")
    args = parser.parse_args()
    path = Path(args.path)
    messages = parse_export(path)
    enq = enqueue(messages)
    print(f"Enqueued {enq} messages from {path}")


if __name__ == "__main__":
    main()
