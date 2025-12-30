from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

DB_PATH = Path(os.environ.get("QUEUE_DB_PATH", Path("data") / "queue.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT UNIQUE,
    conversation_id TEXT,
    end_user_handle TEXT,
    channel TEXT DEFAULT 'web_chat',
    message_direction TEXT DEFAULT 'inbound',
    message_type TEXT DEFAULT 'text',
    payload TEXT,
    raw_payload TEXT,
    status TEXT DEFAULT 'queued',
    processor_id TEXT,
    started_at TEXT,
    finished_at TEXT,
    delivery_status TEXT DEFAULT 'pending',
    delivery_route TEXT,
    response_payload TEXT,
    response_metadata TEXT,
    latency_seconds REAL,
    quality_score REAL,
    matched TEXT,
    missing TEXT,
    ingest_signature TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS conversation_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_status ON queue(status);
CREATE INDEX IF NOT EXISTS idx_conversation ON queue(conversation_id);
CREATE INDEX IF NOT EXISTS idx_history_conversation ON conversation_history(conversation_id, created_at);
"""

ALLOWED_UPDATE_FIELDS = {
    "message_id",
    "conversation_id",
    "end_user_handle",
    "channel",
    "message_direction",
    "message_type",
    "payload",
    "raw_payload",
    "status",
    "processor_id",
    "started_at",
    "finished_at",
    "delivery_status",
    "delivery_route",
    "response_payload",
    "response_metadata",
    "latency_seconds",
    "quality_score",
    "matched",
    "missing",
    "ingest_signature",
    "created_at",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def get_connection() -> sqlite3.Connection:
    """Create a connection with sane defaults for concurrent access."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db() -> None:
    """Ensure the queue table and indexes exist."""
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def insert_message(payload: Dict[str, Any]) -> int:
    """Insert a new inbound message and return its row id."""
    init_db()
    conn = get_connection()
    try:
        cursor = conn.cursor()
        now = _now_iso()
        message_id = payload.get("message_id") or str(uuid4())
        cursor.execute(
            """
            INSERT INTO queue (
                message_id,
                conversation_id,
                end_user_handle,
                channel,
                message_direction,
                message_type,
                payload,
                raw_payload,
                status,
                processor_id,
                started_at,
                delivery_status,
                ingest_signature,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                payload.get("conversation_id") or "",
                payload.get("end_user_handle") or "",
                payload.get("channel") or "web_chat",
                payload.get("message_direction") or "inbound",
                payload.get("message_type") or "text",
                payload.get("text") or payload.get("payload") or "",
                payload.get("raw_payload") or "",
                "queued",
                payload.get("processor_id") or "",
                now,
                "pending",
                payload.get("ingest_signature") or "",
                now,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def claim_row(processor_id: str) -> Optional[Dict[str, Any]]:
    """
    Atomically claim the oldest queued row.
    Returns the row data (as a dict) or None when nothing is queued.
    """
    init_db()
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM queue WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1"
        )
        row = cursor.fetchone()
        if not row:
            conn.rollback()
            return None

        row_id = row["id"]
        now = _now_iso()
        cursor.execute(
            "UPDATE queue SET status = 'processing', processor_id = ?, started_at = ? WHERE id = ?",
            (processor_id, now, row_id),
        )
        conn.commit()

        data = _row_to_dict(row)
        data.update({"status": "processing", "processor_id": processor_id, "started_at": now})
        return data
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_row_status(row_id: int, status: str, **kwargs: Any) -> None:
    """Update status and any supported fields on a queue row."""
    updates: Dict[str, Any] = {"status": status}
    for key, value in kwargs.items():
        if key not in ALLOWED_UPDATE_FIELDS:
            continue
        updates[key] = _maybe_json_dump(key, value)

    if "finished_at" not in updates and status in {"responded", "delivered", "handoff"}:
        updates["finished_at"] = _now_iso()

    if not updates:
        return

    assignments = ", ".join(f"{col} = ?" for col in updates.keys())
    params = list(updates.values()) + [row_id]

    conn = get_connection()
    try:
        conn.execute(f"UPDATE queue SET {assignments} WHERE id = ?", params)
        conn.commit()
    finally:
        conn.close()


def get_conversation_history(conversation_id: str, *, limit: int = 6, exclude_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Return recent messages in a conversation for context/history."""
    if not conversation_id:
        return []
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT conversation_id, role, content, created_at
            FROM conversation_history
            WHERE conversation_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (conversation_id, max(limit, 1)),
        )
        rows = cursor.fetchall()
        if not rows:
            return []
        ordered = list(reversed(rows))
        return [_row_to_dict(row) for row in ordered]
    finally:
        conn.close()


def append_history(conversation_id: str, role: str, content: str) -> None:
    """Append a single message into the conversation_history table."""
    if not conversation_id or not role or not content:
        return
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO conversation_history (conversation_id, role, content, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (conversation_id, role, content, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def bulk_append_history(messages: List[Dict[str, str]]) -> None:
    """Append many messages at once."""
    if not messages:
        return
    payloads = [
        (
            msg.get("conversation_id"),
            msg.get("role"),
            msg.get("content"),
            msg.get("created_at") or _now_iso(),
        )
        for msg in messages
        if msg.get("conversation_id") and msg.get("role") and msg.get("content")
    ]
    if not payloads:
        return
    conn = get_connection()
    try:
        conn.executemany(
            """
            INSERT INTO conversation_history (conversation_id, role, content, created_at)
            VALUES (?, ?, ?, ?)
            """,
            payloads,
        )
        conn.commit()
    finally:
        conn.close()


def _maybe_json_dump(key: str, value: Any) -> Any:
    """Serialize JSON-friendly fields to strings to align with the Excel format."""
    if key in {"matched", "missing", "response_payload", "response_metadata"}:
        if value in (None, "", [], {}):
            return ""
        try:
            return json.dumps(value, ensure_ascii=False)
        except TypeError:
            return str(value)
    return value


# Ensure the schema exists when the module is imported for the first time.
init_db()
