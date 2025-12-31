from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from . import config

DB_PATH = Path(config.DB_PATH)

SCHEMA = """
CREATE TABLE IF NOT EXISTS queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT,
    message_id TEXT UNIQUE,
    idempotency_key TEXT,
    retry_count INTEGER DEFAULT 0,
    available_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
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
    triage_json TEXT,
    draft_customer_reply_subject TEXT,
    draft_customer_reply_body TEXT,
    triage_draft_subject TEXT,
    triage_draft_body TEXT,
    review_final_subject TEXT,
    review_final_body TEXT,
    missing_info_questions TEXT,
    llm_model TEXT,
    prompt_version TEXT,
    redaction_applied INTEGER,
    ingest_signature TEXT,
    review_action TEXT,
    reviewed_at TEXT,
    reviewer TEXT,
    review_notes TEXT,
    error_tags TEXT,
    diff_subject_ratio REAL,
    diff_body_ratio REAL,
    sent_body TEXT,
    edit_distance REAL,
    feedback_source TEXT,
    closed_loop_at TEXT,
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
    "case_id",
    "message_id",
    "idempotency_key",
    "retry_count",
    "available_at",
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
    "triage_json",
    "draft_customer_reply_subject",
    "draft_customer_reply_body",
    "triage_draft_subject",
    "triage_draft_body",
    "review_final_subject",
    "review_final_body",
    "missing_info_questions",
    "llm_model",
    "prompt_version",
    "redaction_applied",
    "triage_mode",
    "llm_latency_ms",
    "llm_attempts",
    "schema_valid",
    "redacted_payload",
    "evidence_json",
    "evidence_sources_run",
    "evidence_created_at",
    "final_report_json",
    "ingest_signature",
    "review_action",
    "reviewed_at",
    "reviewer",
    "review_notes",
    "error_tags",
    "diff_subject_ratio",
    "diff_body_ratio",
    "sent_body",
    "edit_distance",
    "feedback_source",
    "closed_loop_at",
    "created_at",
}

ALLOWED_STATUS_TRANSITIONS = {
    "queued": {"processing", "queued", "dead_letter"},
    "processing": {"triaged", "queued", "dead_letter", "responded", "handoff"},
    "triaged": {"awaiting_human", "approved", "rewrite", "escalate_pending", "triaged", "responded"},
    "awaiting_human": {"approved", "rewrite", "escalate_pending", "awaiting_human", "responded"},
    "approved": {"responded", "approved"},
    "rewrite": {"triaged", "rewrite"},
    "escalate_pending": {"triaged", "escalate_pending"},
    "responded": {"delivered", "responded"},
    "delivered": {"delivered"},
    "handoff": {"delivered", "responded", "handoff"},
    "dead_letter": {"dead_letter"},
}

TRIAGE_COMPLETE_STATES = {"triaged", "awaiting_human", "approved", "rewrite", "escalate_pending", "responded", "delivered"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _compute_idempotency_key(payload: Dict[str, Any], created_at: str) -> str:
    text = (payload.get("text") or payload.get("payload") or "").strip()
    tenant = payload.get("end_user_handle") or payload.get("tenant") or payload.get("customer") or ""
    bucket = created_at[:10]
    raw = f"{tenant}|{text[:200]}|{bucket}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
        _ensure_columns(conn)
        conn.commit()
    finally:
        conn.close()


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Add new columns introduced after initial table creation."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(queue)")
    existing = {row["name"] for row in cursor.fetchall()}
    desired = {
        "case_id": "TEXT",
        "idempotency_key": "TEXT",
        "retry_count": "INTEGER",
        "available_at": "TEXT",
        "triage_json": "TEXT",
        "draft_customer_reply_subject": "TEXT",
        "draft_customer_reply_body": "TEXT",
        "triage_draft_subject": "TEXT",
        "triage_draft_body": "TEXT",
        "review_final_subject": "TEXT",
        "review_final_body": "TEXT",
        "missing_info_questions": "TEXT",
        "llm_model": "TEXT",
        "prompt_version": "TEXT",
        "redaction_applied": "INTEGER",
        "triage_mode": "TEXT",
        "llm_latency_ms": "INTEGER",
        "llm_attempts": "INTEGER",
        "schema_valid": "INTEGER",
        "redacted_payload": "TEXT",
        "evidence_json": "TEXT",
        "evidence_sources_run": "TEXT",
        "evidence_created_at": "TEXT",
        "final_report_json": "TEXT",
        "review_action": "TEXT",
        "reviewed_at": "TEXT",
        "reviewer": "TEXT",
        "review_notes": "TEXT",
        "error_tags": "TEXT",
        "diff_subject_ratio": "REAL",
        "diff_body_ratio": "REAL",
        "sent_body": "TEXT",
        "edit_distance": "REAL",
        "feedback_source": "TEXT",
        "closed_loop_at": "TEXT",
    }
    for name, col_type in desired.items():
        if name not in existing:
            cursor.execute(f"ALTER TABLE queue ADD COLUMN {name} {col_type}")


def get_by_idempotency(idempotency_key: str) -> Optional[Dict[str, Any]]:
    """Return the most recent row matching an idempotency key."""
    if not idempotency_key:
        return None
    init_db()
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM queue
            WHERE idempotency_key = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (idempotency_key,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return _row_to_dict(row)
    finally:
        conn.close()


def insert_message(payload: Dict[str, Any]) -> Tuple[int, bool]:
    """Insert a new inbound message and return (row id, created?)."""
    init_db()
    conn = get_connection()
    try:
        cursor = conn.cursor()
        now = _now_iso()
        message_id = payload.get("message_id") or str(uuid4())
        case_id = payload.get("case_id") or message_id
        idempotency_key = payload.get("idempotency_key") or _compute_idempotency_key(payload, now)

        existing = get_by_idempotency(idempotency_key)
        if existing and existing.get("status") != "dead_letter":
            return int(existing["id"]), False

        cursor.execute(
            """
            INSERT INTO queue (
                case_id,
                message_id,
                idempotency_key,
                available_at,
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
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                case_id,
                message_id,
                idempotency_key,
                now,
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
        return int(cursor.lastrowid), True
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
            """
            SELECT * FROM queue
            WHERE status = 'queued' AND (available_at IS NULL OR available_at <= ?)
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (_now_iso(),),
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
    new_status = str(status)
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM queue WHERE id = ?", (row_id,))
        existing_row = cursor.fetchone()
        if not existing_row:
            raise ValueError(f"Queue row {row_id} not found")
        current_status = str(existing_row["status"] or "").lower()
        target_status = new_status.lower()

        if target_status != current_status:
            allowed = ALLOWED_STATUS_TRANSITIONS.get(current_status, set())
            if target_status not in allowed:
                raise ValueError(f"Invalid status transition {current_status} -> {target_status}")

        existing = _row_to_dict(existing_row)
    finally:
        conn.close()

    updates: Dict[str, Any] = {"status": status}
    for key, value in kwargs.items():
        if key not in ALLOWED_UPDATE_FIELDS:
            continue
        updates[key] = _maybe_json_dump(key, value)

    if target_status in TRIAGE_COMPLETE_STATES:
        triage_json = kwargs.get("triage_json") or existing.get("triage_json")
        draft_subject = kwargs.get("triage_draft_subject") or kwargs.get("draft_customer_reply_subject") or existing.get("triage_draft_subject") or existing.get("draft_customer_reply_subject")
        draft_body = kwargs.get("triage_draft_body") or kwargs.get("draft_customer_reply_body") or existing.get("triage_draft_body") or existing.get("draft_customer_reply_body")
        if not triage_json:
            raise ValueError(f"triage_json is required when setting status to {status}")
        if not draft_subject or not draft_body:
            raise ValueError(f"triage draft subject/body required when setting status to {status}")

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


def fetch_queue(limit: int = 100) -> List[Dict[str, Any]]:
    """Return recent queue rows for UI consumption."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT *
            FROM queue
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(limit, 1),),
        )
        rows = cursor.fetchall()
        return [_row_to_dict(row) for row in rows]
    finally:
        conn.close()


def _maybe_json_dump(key: str, value: Any) -> Any:
    """Serialize JSON-friendly fields to strings to align with the Excel format."""
    if key in {
        "matched",
        "missing",
        "response_payload",
        "response_metadata",
        "triage_json",
        "missing_info_questions",
        "redacted_payload",
        "evidence_json",
        "evidence_sources_run",
        "final_report_json",
        "error_tags",
    }:
        if value in (None, "", [], {}):
            return ""
        try:
            return json.dumps(value, ensure_ascii=False)
        except TypeError:
            return str(value)
    return value


# Ensure the schema exists when the module is imported for the first time.
init_db()
