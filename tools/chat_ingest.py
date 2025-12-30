#!/usr/bin/env python3
"""Demo ingestion script that writes chat messages into the Excel queue."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List

import pandas as pd

from app import queue_db
from tools import chat_worker
from tools.process_queue import save_queue

USE_DB_QUEUE = os.environ.get("USE_DB_QUEUE", "true").lower() == "true"


def _load_queue(queue_path: Path) -> pd.DataFrame:
    if queue_path.exists():
        df = pd.read_excel(queue_path)
    else:
        df = pd.DataFrame()
    return chat_worker.ensure_chat_columns(df)


def _make_row(
    conversation_id: str,
    text: str,
    *,
    end_user_handle: str,
    channel: str,
) -> dict:
    now_iso = datetime.now(timezone.utc).isoformat()
    return {
        "conversation_id": conversation_id,
        "message_id": "",
        "end_user_handle": end_user_handle,
        "channel": channel,
        "message_direction": "inbound",
        "message_type": "text",
        "payload": text,
        "status": "queued",
        "processor_id": "",
        "started_at": now_iso,
        "finished_at": "",
        "delivery_status": "pending",
    }


def ingest_messages(queue_path: Path, messages: Iterable[dict]) -> int:
    rows = []
    for message in messages:
        conversation_id = str(message.get("conversation_id") or f"demo-{datetime.now(timezone.utc).timestamp():.0f}")
        text = str(message.get("text") or message.get("payload") or "").strip()
        if not text:
            continue
        end_user_handle = str(message.get("end_user_handle") or "demo-user")
        channel = str(message.get("channel") or "web_chat")
        row = _make_row(conversation_id, text, end_user_handle=end_user_handle, channel=channel)
        row["raw_payload"] = str(message.get("raw_payload") or "")
        row["ingest_signature"] = str(message.get("ingest_signature") or "")
        if message.get("message_id"):
            row["message_id"] = str(message.get("message_id"))
        rows.append(row)

    if not rows:
        return 0

    if USE_DB_QUEUE:
        for row in rows:
            queue_db.insert_message(
                {
                    "message_id": row.get("message_id") or "",
                    "conversation_id": row.get("conversation_id"),
                    "end_user_handle": row.get("end_user_handle"),
                    "channel": row.get("channel"),
                    "message_direction": row.get("message_direction", "inbound"),
                    "message_type": row.get("message_type", "text"),
                    "text": row.get("payload", ""),
                    "raw_payload": row.get("raw_payload", ""),
                    "ingest_signature": row.get("ingest_signature", ""),
                }
            )
        return len(rows)

    queue_df = _load_queue(queue_path)
    combined = pd.concat([queue_df, pd.DataFrame(rows)], ignore_index=True)
    save_queue(queue_path, combined)
    return len(rows)


def parse_messages(args: argparse.Namespace) -> List[dict]:
    if args.messages:
        return [
            {
                "conversation_id": args.conversation_id or "demo-web",
                "text": message,
                "end_user_handle": args.end_user_handle,
                "channel": args.channel,
            }
            for message in args.messages
        ]
    if args.json_input:
        path = Path(args.json_input)
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return raw
        raise SystemExit("JSON input must be a list of message dicts")
    return []


def main() -> None:
    parser = argparse.ArgumentParser(description="Inject chat messages into the Excel queue")
    parser.add_argument("--queue", default="data/email_queue.xlsx", help="Queue workbook path")
    parser.add_argument("messages", nargs="*", help="Inline chat messages to enqueue")
    parser.add_argument("--json-input", help="Path to JSON file with chat message objects")
    parser.add_argument("--conversation-id", help="Conversation id to reuse for inline messages")
    parser.add_argument("--end-user-handle", default="demo-user", help="Simulated end-user identifier")
    parser.add_argument("--channel", default="web_chat", help="Channel label")
    args = parser.parse_args()

    messages = parse_messages(args)
    if not messages:
        print("No messages to ingest")
        return

    count = ingest_messages(Path(args.queue), messages)
    print(f"Enqueued {count} chat message(s) -> {args.queue}")


if __name__ == "__main__":
    main()
__all__ = ["ingest_messages", "parse_messages"]
