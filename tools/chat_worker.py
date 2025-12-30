#!/usr/bin/env python3
"""Queue worker that reuses the chat service to answer inbound messages."""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import pandas as pd

from app import queue_db
from app.chat_service import ChatMessage, ChatService
from tools.process_queue import save_queue


CHAT_DEFAULTS: Dict[str, object] = {
    "message_id": "",
    "conversation_id": "",
    "end_user_handle": "",
    "channel": "web_chat",
    "message_direction": "inbound",
    "message_type": "text",
    "payload": "",
    "raw_payload": "",
    "language": "",
    "language_source": "",
    "language_confidence": None,
    "conversation_tags": "[]",
    "status": "queued",
    "processor_id": "",
    "started_at": "",
    "finished_at": "",
    "latency_seconds": None,
    "quality_score": None,
    "matched": "[]",
    "missing": "[]",
    "response_payload": "",
    "response_metadata": "",
    "delivery_route": "",
    "delivery_status": "pending",
    "ingest_signature": "",
}

CHAT_STRING_COLUMNS = {
    "message_id",
    "conversation_id",
    "end_user_handle",
    "channel",
    "message_direction",
    "message_type",
    "payload",
    "raw_payload",
    "language",
    "language_source",
    "conversation_tags",
    "status",
    "processor_id",
    "started_at",
    "finished_at",
    "matched",
    "missing",
    "response_payload",
    "response_metadata",
    "delivery_route",
    "delivery_status",
    "ingest_signature",
}

CHAT_NUMERIC_COLUMNS = {"language_confidence", "latency_seconds", "quality_score"}

CHAT_JSON_COLUMNS = {"conversation_tags", "matched", "missing", "response_payload", "response_metadata"}

USE_DB_QUEUE = os.environ.get("USE_DB_QUEUE", "true").lower() == "true"


def _load_queue(queue_path: Path) -> pd.DataFrame:
    if not queue_path.exists():
        return ensure_chat_columns(pd.DataFrame(columns=list(CHAT_DEFAULTS.keys())))
    try:
        df = pd.read_excel(queue_path)
    except Exception as exc:  # pragma: no cover - surface for operators
        print(f"Warning: unable to read queue workbook {queue_path}: {exc}")
        return ensure_chat_columns(pd.DataFrame(columns=list(CHAT_DEFAULTS.keys())))
    return ensure_chat_columns(df)


def ensure_chat_columns(df: pd.DataFrame) -> pd.DataFrame:
    for column, default in CHAT_DEFAULTS.items():
        if column not in df.columns:
            df[column] = default
    for column in CHAT_STRING_COLUMNS:
        df[column] = df[column].astype("object").where(df[column].notna(), "")
    for column in CHAT_NUMERIC_COLUMNS:
        if column not in df.columns:
            df[column] = pd.NA
    return df


def _json_load(value: object) -> object:
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _json_dump(value: object) -> str:
    if value in (None, ""):
        return ""
    return json.dumps(value, ensure_ascii=False)


def _conversation_history(df: pd.DataFrame, conversation_id: str, current_index: int, limit: int = 6) -> List[ChatMessage]:
    if not conversation_id:
        return []
    history_rows = (
        df[df["conversation_id"].astype(str) == conversation_id]
        .drop(index=current_index, errors="ignore")
        .copy()
    )
    if history_rows.empty:
        return []
    if "finished_at" in history_rows.columns:
        history_rows = history_rows.sort_values(by="finished_at", ascending=True)
    messages: List[ChatMessage] = []
    for _, row in history_rows.tail(limit).iterrows():
        direction = str(row.get("message_direction", "")).lower()
        role = "system"
        if direction == "inbound":
            role = "user"
        elif direction in ("outbound", "assistant"):
            role = "assistant"
        content = str(row.get("payload", ""))
        if not content:
            content = str(row.get("body", ""))
        metadata = {
            "delivery_status": str(row.get("delivery_status", "")),
            "channel": str(row.get("channel", "")),
        }
        finished_at = str(row.get("finished_at", ""))
        timestamp = datetime.fromisoformat(finished_at.replace("Z", "+00:00")) if finished_at else datetime.now(timezone.utc)
        messages.append(ChatMessage(role=role, content=content, timestamp=timestamp, metadata=metadata))
    return messages


def _compose_metadata(row: pd.Series) -> Dict[str, str]:
    metadata: Dict[str, str] = {}
    for key in ("language", "language_source", "language_confidence", "conversation_tags", "ingest_signature"):
        value = row.get(key)
        if pd.isna(value):
            continue
        if value is None:
            continue
        metadata[key] = str(value)
    metadata["raw"] = str(row.get("raw_payload", ""))
    return metadata


def _claim_row(df: pd.DataFrame, processor_id: str) -> Optional[int]:
    if "status" not in df.columns:
        return None
    status_series = df["status"].astype(str).str.lower()
    queued_indices = df.index[status_series.isin(["", "nan", "queued"])]
    if queued_indices.empty:
        return None
    idx = int(queued_indices[0])
    timestamp = datetime.now(timezone.utc).isoformat()
    df.loc[idx, "status"] = "processing"
    df.loc[idx, "processor_id"] = processor_id
    df.loc[idx, "started_at"] = timestamp
    return idx


def process_once(queue_path: Path, *, processor_id: str, chat_service: Optional[ChatService] = None) -> bool:
    chat_service = chat_service or ChatService()
    if USE_DB_QUEUE:
        return _process_once_db(processor_id=processor_id, chat_service=chat_service)

    df = _load_queue(queue_path)
    idx = _claim_row(df, processor_id)
    if idx is None:
        return False

    save_queue(queue_path, df)

    row = df.loc[idx].copy()
    started_at = row.get("started_at") or datetime.now(timezone.utc).isoformat()
    conversation_id = str(row.get("conversation_id") or row.get("ingest_signature") or row.get("id") or uuid4())
    df.loc[idx, "conversation_id"] = conversation_id

    user_text = str(row.get("payload") or row.get("body") or row.get("raw_payload") or "").strip()
    if not user_text:
        user_text = "Hi"
    metadata = _compose_metadata(row)
    history = _conversation_history(df, conversation_id, idx)

    user_message = ChatMessage(role="user", content=user_text, metadata=metadata)

    start = time.perf_counter()
    result = chat_service.respond(history, user_message, conversation_id=conversation_id, channel=str(row.get("channel") or "web_chat"))
    elapsed = time.perf_counter() - start

    record = chat_service.build_queue_record(
        user_message,
        result,
        conversation_id=conversation_id,
        end_user_handle=str(row.get("end_user_handle") or row.get("customer") or ""),
        channel=str(row.get("channel") or "web_chat"),
    )

    finished_at = datetime.now(timezone.utc).isoformat()
    df.loc[idx, "message_id"] = record.get("message_id", str(uuid4()))
    df.loc[idx, "conversation_id"] = conversation_id
    df.loc[idx, "end_user_handle"] = record.get("end_user_handle", "")
    df.loc[idx, "channel"] = record.get("channel", "web_chat")
    df.loc[idx, "message_direction"] = "inbound"
    df.loc[idx, "message_type"] = row.get("message_type", "text")
    df.loc[idx, "payload"] = user_text
    df.loc[idx, "raw_payload"] = row.get("raw_payload", "")
    df.loc[idx, "status"] = record.get("status", "responded")
    df.loc[idx, "processor_id"] = processor_id
    df.loc[idx, "finished_at"] = finished_at
    df.loc[idx, "latency_seconds"] = elapsed
    df.loc[idx, "quality_score"] = record.get("quality_score")
    df.loc[idx, "matched"] = _json_dump(record.get("matched") or result.evaluation.get("matched") if result.evaluation else None)
    df.loc[idx, "missing"] = _json_dump(record.get("missing") or result.evaluation.get("missing") if result.evaluation else None)
    df.loc[idx, "response_payload"] = _json_dump(record.get("response_payload") or {"type": "text", "content": result.response.content})
    df.loc[idx, "response_metadata"] = _json_dump(record.get("response_metadata") or result.evaluation)
    df.loc[idx, "delivery_route"] = record.get("delivery_route", "")
    df.loc[idx, "delivery_status"] = record.get("delivery_status", "pending")
    df.loc[idx, "started_at"] = started_at

    save_queue(queue_path, df)

    status = df.loc[idx, "status"]
    print(f"Processed conversation {conversation_id} -> status={status} latency={elapsed:.3f}s")
    return True


def _compose_metadata_mapping(row: Dict[str, Any]) -> Dict[str, str]:
    metadata: Dict[str, str] = {}
    for key in ("language", "language_source", "language_confidence", "conversation_tags", "ingest_signature"):
        value = row.get(key)
        if value is None:
            continue
        metadata[key] = str(value)
    metadata["raw"] = str(row.get("raw_payload", ""))
    return metadata


def _conversation_history_from_records(rows: List[Dict[str, Any]], limit: int = 6) -> List[ChatMessage]:
    if not rows:
        return []
    messages: List[ChatMessage] = []
    for row in rows[-limit:]:
        role = str(row.get("role") or row.get("message_direction") or "").lower()
        if role not in ("user", "assistant"):
            direction = role
            role = "assistant" if direction in ("outbound", "assistant") else "user"
        content = str(row.get("content") or row.get("payload", "") or row.get("body", ""))
        metadata = {
            "delivery_status": str(row.get("delivery_status", "")),
            "channel": str(row.get("channel", "")),
        }
        finished_at = str(row.get("created_at", "") or row.get("finished_at", "") or row.get("started_at", ""))
        timestamp = datetime.fromisoformat(finished_at.replace("Z", "+00:00")) if finished_at else datetime.now(timezone.utc)
        messages.append(ChatMessage(role=role, content=content, timestamp=timestamp, metadata=metadata))
    return messages


def _process_once_db(*, processor_id: str, chat_service: ChatService) -> bool:
    row = queue_db.claim_row(processor_id)
    if not row:
        return False

    started_at = row.get("started_at") or datetime.now(timezone.utc).isoformat()
    conversation_id = str(row.get("conversation_id") or row.get("ingest_signature") or row.get("message_id") or uuid4())
    if not row.get("conversation_id"):
        queue_db.update_row_status(row["id"], status="processing", conversation_id=conversation_id)

    user_text = str(row.get("payload") or row.get("body") or row.get("raw_payload") or row.get("text") or "").strip()
    if not user_text:
        user_text = "Hi"
    metadata = _compose_metadata_mapping(row)
    history_rows = queue_db.get_conversation_history(conversation_id, limit=6)
    history = _conversation_history_from_records(history_rows)

    user_message = ChatMessage(role="user", content=user_text, metadata=metadata)

    start = time.perf_counter()
    result = chat_service.respond(history, user_message, conversation_id=conversation_id, channel=str(row.get("channel") or "web_chat"))
    elapsed = time.perf_counter() - start

    record = chat_service.build_queue_record(
        user_message,
        result,
        conversation_id=conversation_id,
        end_user_handle=str(row.get("end_user_handle") or row.get("customer") or ""),
        channel=str(row.get("channel") or "web_chat"),
    )

    queue_db.append_history(conversation_id, "user", user_text)
    queue_db.append_history(conversation_id, "assistant", result.response.content)

    finished_at = datetime.now(timezone.utc).isoformat()
    matched = record.get("matched") or (result.evaluation.get("matched") if result.evaluation else None)
    missing = record.get("missing") or (result.evaluation.get("missing") if result.evaluation else None)
    response_payload = record.get("response_payload") or {"type": "text", "content": result.response.content}
    response_metadata = record.get("response_metadata") or result.evaluation

    queue_db.update_row_status(
        row_id=row["id"],
        status=record.get("status", "responded"),
        message_id=record.get("message_id", row.get("message_id") or str(uuid4())),
        conversation_id=conversation_id,
        end_user_handle=record.get("end_user_handle", ""),
        channel=record.get("channel", "web_chat"),
        message_direction="inbound",
        message_type=row.get("message_type", "text"),
        payload=user_text,
        raw_payload=row.get("raw_payload", ""),
        processor_id=processor_id,
        started_at=started_at,
        finished_at=finished_at,
        latency_seconds=elapsed,
        quality_score=record.get("quality_score"),
        matched=matched,
        missing=missing,
        response_payload=response_payload,
        response_metadata=response_metadata,
        delivery_route=record.get("delivery_route", ""),
        delivery_status=record.get("delivery_status", "pending"),
        ingest_signature=row.get("ingest_signature", ""),
    )

    status = record.get("status", "responded")
    print(f"[db] Processed conversation {conversation_id} -> status={status} latency={elapsed:.3f}s")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Process chat turns from the Excel-backed queue")
    parser.add_argument("--queue", default="data/email_queue.xlsx", help="Queue workbook path")
    parser.add_argument("--processor-id", default="chat-worker-1", help="Identifier for this worker")
    parser.add_argument("--watch", action="store_true", help="Keep polling for new queued items")
    parser.add_argument("--poll-interval", type=float, default=3.0, help="Seconds between polls when --watch is set")
    args = parser.parse_args()

    queue_path = Path(args.queue)
    chat_service = ChatService()

    while True:
        processed = process_once(queue_path, processor_id=args.processor_id, chat_service=chat_service)
        if not processed:
            if args.watch:
                time.sleep(max(args.poll_interval, 0.25))
                continue
            print("Queue empty. Nothing to process.")
            break


if __name__ == "__main__":
    main()
