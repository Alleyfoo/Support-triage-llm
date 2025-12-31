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

CREATE TABLE IF NOT EXISTS intakes (
    intake_id TEXT PRIMARY KEY,
    received_at TEXT,
    channel TEXT,
    from_address TEXT,
    from_domain TEXT,
    claimed_domain TEXT,
    subject_raw TEXT,
    body_raw TEXT,
    attachments_json TEXT,
    tenant_id TEXT,
    identity_confidence TEXT DEFAULT 'unknown',
    status TEXT DEFAULT 'new',
    resolution_note TEXT,
    resolved_at TEXT,
    acknowledged_at TEXT,
    acknowledged_by TEXT,
    customer_request_id TEXT,
    error_code TEXT,
    deleted_at TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS evidence_runs (
    evidence_id TEXT PRIMARY KEY,
    intake_id TEXT,
    tool_name TEXT,
    params_json TEXT,
    ran_at TEXT,
    expires_at TEXT,
    time_bucket TEXT,
    params_hash TEXT,
    status TEXT,
    result_json_internal TEXT,
    summary_external TEXT,
    summary_internal TEXT,
    redaction_level TEXT DEFAULT 'internal',
    result_hash TEXT,
    ttl_seconds INTEGER,
    error_message TEXT,
    replays_evidence_id TEXT
);

CREATE TABLE IF NOT EXISTS handoff_packs (
    handoff_id TEXT PRIMARY KEY,
    intake_id TEXT,
    created_at TEXT,
    expires_at TEXT,
    tier INTEGER,
    payload_json TEXT,
    sent_to TEXT,
    status TEXT DEFAULT 'created'
);

CREATE TABLE IF NOT EXISTS tenants (
    tenant_id TEXT PRIMARY KEY,
    primary_domain TEXT,
    domains_json TEXT,
    entitled_services_json TEXT,
    default_region TEXT
);

CREATE TABLE IF NOT EXISTS replay_audit (
    id TEXT PRIMARY KEY,
    api_key_hash TEXT,
    evidence_id TEXT,
    new_evidence_id TEXT,
    ts TEXT,
    result TEXT,
    reason TEXT,
    remote_ip TEXT,
    user_agent TEXT
);

CREATE TABLE IF NOT EXISTS service_breakers (
    service_id TEXT,
    scope TEXT,
    consecutive_failures INTEGER,
    opened_at TEXT,
    cooldown_until TEXT,
    last_error_kind TEXT,
    updated_at TEXT,
    PRIMARY KEY(service_id, scope)
);
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

    cursor.execute("PRAGMA table_info(evidence_runs)")
    ev_existing = {row["name"] for row in cursor.fetchall()}
    ev_desired = {
        "time_bucket": "TEXT",
        "params_hash": "TEXT",
        "replays_evidence_id": "TEXT",
    }
    for name, col_type in ev_desired.items():
        if name not in ev_existing:
            cursor.execute(f"ALTER TABLE evidence_runs ADD COLUMN {name} {col_type}")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_cache ON evidence_runs (tool_name, params_hash, time_bucket)")

    cursor.execute("PRAGMA table_info(intakes)")
    int_existing = {row["name"] for row in cursor.fetchall()}
    int_desired = {
        "status": "TEXT",
        "resolution_note": "TEXT",
        "resolved_at": "TEXT",
        "acknowledged_at": "TEXT",
        "acknowledged_by": "TEXT",
        "customer_request_id": "TEXT",
        "error_code": "TEXT",
        "deleted_at": "TEXT",
    }
    for name, col_type in int_desired.items():
        if name not in int_existing:
            cursor.execute(f"ALTER TABLE intakes ADD COLUMN {name} {col_type}")

    cursor.execute("PRAGMA table_info(evidence_runs)")
    ev_existing = {row["name"] for row in cursor.fetchall()}
    ev_desired = {
        "expires_at": "TEXT",
    }
    for name, col_type in ev_desired.items():
        if name not in ev_existing:
            cursor.execute(f"ALTER TABLE evidence_runs ADD COLUMN {name} {col_type}")

    cursor.execute("PRAGMA table_info(handoff_packs)")
    hp_existing = {row["name"] for row in cursor.fetchall()}
    hp_desired = {"expires_at": "TEXT"}
    for name, col_type in hp_desired.items():
        if name not in hp_existing:
            cursor.execute(f"ALTER TABLE handoff_packs ADD COLUMN {name} {col_type}")

    cursor.execute("PRAGMA table_info(service_breakers)")
    sb_existing = {row["name"] for row in cursor.fetchall()}
    if "service_id" not in sb_existing:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS service_breakers (
                service_id TEXT,
                scope TEXT,
                consecutive_failures INTEGER,
                opened_at TEXT,
                cooldown_until TEXT,
                last_error_kind TEXT,
                updated_at TEXT,
                PRIMARY KEY(service_id, scope)
            )
            """
        )


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


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def upsert_tenant(tenant_id: str, primary_domain: str, domains: List[str], entitled_services: List[str], default_region: Optional[str] = None) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO tenants (tenant_id, primary_domain, domains_json, entitled_services_json, default_region)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(tenant_id) DO UPDATE SET primary_domain=excluded.primary_domain, domains_json=excluded.domains_json, entitled_services_json=excluded.entitled_services_json, default_region=excluded.default_region
            """,
            (tenant_id, primary_domain, json.dumps(domains), json.dumps(entitled_services), default_region),
        )
        conn.commit()
    finally:
        conn.close()


def _load_tenants() -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tenants")
        rows = cursor.fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def _domain_from_email(addr: str) -> str:
    if not addr or "@" not in addr:
        return ""
    return (addr.split("@", 1)[1] or "").lower()


def insert_intake(
    *,
    received_at: str,
    channel: str,
    from_address: str,
    claimed_domain: Optional[str],
    subject_raw: str,
    body_raw: str,
    attachments_json: Optional[str] = None,
    customer_request_id: Optional[str] = None,
    error_code: Optional[str] = None,
) -> str:
    intake_id = str(uuid4())
    now = _now_iso()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO intakes (intake_id, received_at, channel, from_address, from_domain, claimed_domain, subject_raw, body_raw, attachments_json, tenant_id, identity_confidence, status, resolution_note, resolved_at, customer_request_id, error_code, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'unknown', 'new', '', NULL, ?, ?, ?, ?)
            """,
            (
                intake_id,
                received_at,
                channel,
                from_address,
                _domain_from_email(from_address),
                claimed_domain or "",
                subject_raw,
                body_raw,
                attachments_json or "[]",
                customer_request_id or "",
                error_code or "",
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return intake_id


def update_intake_tenant(intake_id: str, tenant_id: Optional[str], identity_confidence: str) -> None:
    now = _now_iso()
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE intakes SET tenant_id = ?, identity_confidence = ?, updated_at = ? WHERE intake_id = ?
            """,
            (tenant_id, identity_confidence, now, intake_id),
        )
        conn.commit()
    finally:
        conn.close()


def resolve_tenant(intake: Dict[str, Any]) -> Dict[str, Any]:
    tenants = _load_tenants()
    from_domain = (intake.get("from_domain") or "").lower()
    claimed_domain = (intake.get("claimed_domain") or "").lower()
    for tenant in tenants:
        domains = json.loads(tenant.get("domains_json") or "[]") or []
        if from_domain and from_domain in [d.lower() for d in domains]:
            return {"tenant_id": tenant["tenant_id"], "confidence": "high", "entitled_services": json.loads(tenant.get("entitled_services_json") or "[]"), "default_region": tenant.get("default_region")}
    for tenant in tenants:
        domains = json.loads(tenant.get("domains_json") or "[]") or []
        if claimed_domain and claimed_domain in [d.lower() for d in domains]:
            return {"tenant_id": tenant["tenant_id"], "confidence": "low", "entitled_services": json.loads(tenant.get("entitled_services_json") or "[]"), "default_region": tenant.get("default_region")}
    return {"tenant_id": None, "confidence": "unknown", "entitled_services": [], "default_region": None}


def record_evidence_run(
    *,
    intake_id: str,
    tool_name: str,
    params: Dict[str, Any],
    result: Optional[Dict[str, Any]],
    summary_external: str = "",
    summary_internal: Optional[str] = None,
    status: str = "ok",
    redaction_level: str = "internal",
    ttl_seconds: Optional[int] = None,
    error_message: Optional[str] = None,
    replays_evidence_id: Optional[str] = None,
    cache_bucketed: bool = True,
) -> Dict[str, Any]:
    params_json = _canonical_json(params)
    params_hash = hashlib.sha256(params_json.encode("utf-8")).hexdigest()
    evidence_id = str(uuid4())
    ran_at = _now_iso()
    expires_at = (datetime.fromisoformat(ran_at.replace("Z", "+00:00")) + timedelta(days=config.EVIDENCE_TTL_DAYS)).isoformat().replace("+00:00", "Z")
    time_bucket = ran_at[:16] if cache_bucketed else ran_at
    result_json = _canonical_json(result or {})
    digest_input = _canonical_json({"params": params, "result": result or {}})
    result_hash = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()

    cache_hit = False
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """
                INSERT INTO evidence_runs (evidence_id, intake_id, tool_name, params_json, ran_at, expires_at, time_bucket, params_hash, status, result_json_internal, summary_external, summary_internal, redaction_level, result_hash, ttl_seconds, error_message, replays_evidence_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    intake_id,
                    tool_name,
                    params_json,
                    ran_at,
                    expires_at,
                    time_bucket,
                    params_hash,
                    status,
                    result_json,
                    summary_external,
                    summary_internal or "",
                    redaction_level,
                    result_hash,
                    ttl_seconds,
                    error_message,
                    replays_evidence_id,
                ),
            )
            conn.commit()
            cache_hit = False
        except sqlite3.IntegrityError:
            # Another process inserted same tool/params bucket; reuse latest
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM evidence_runs
                WHERE tool_name = ? AND params_hash = ? AND time_bucket = ?
                ORDER BY ran_at DESC
                LIMIT 1
                """,
                (tool_name, params_hash, time_bucket),
            )
            row = cursor.fetchone()
            conn.rollback()
            if row:
                data = _row_to_dict(row)
                data["cache_hit"] = True
                data["checked_at"] = data.get("ran_at")
                return data
            else:
                raise
    finally:
        conn.close()

    return {
        "evidence_id": evidence_id,
        "intake_id": intake_id,
        "tool_name": tool_name,
        "params_json": params_json,
        "params_hash": params_hash,
        "time_bucket": time_bucket,
        "expires_at": expires_at,
        "ran_at": ran_at,
        "checked_at": ran_at,
        "status": status,
        "result_json_internal": result_json,
        "summary_external": summary_external,
        "summary_internal": summary_internal or "",
        "redaction_level": redaction_level,
        "result_hash": result_hash,
        "ttl_seconds": ttl_seconds,
        "error_message": error_message,
        "replays_evidence_id": replays_evidence_id,
        "cache_hit": cache_hit,
    }


def create_handoff_pack(*, intake_id: str, tier: int, payload_json: Dict[str, Any], sent_to: Optional[str] = None, status: str = "created") -> str:
    handoff_id = str(uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO handoff_packs (handoff_id, intake_id, created_at, expires_at, tier, payload_json, sent_to, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                handoff_id,
                intake_id,
                _now_iso(),
                (datetime.now(timezone.utc) + timedelta(days=config.HANDOFF_TTL_DAYS)).isoformat().replace("+00:00", "Z"),
                tier,
                _canonical_json(payload_json),
                sent_to,
                status,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return handoff_id


def get_evidence_by_id(evidence_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM evidence_runs WHERE evidence_id = ?", (evidence_id,))
        row = cursor.fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def get_service_breaker(service_id: str, scope: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM service_breakers WHERE service_id = ? AND scope = ?", (service_id, scope))
        row = cursor.fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def bump_service_breaker_failure(service_id: str, scope: str, now_dt: datetime, threshold: int, cooldown_seconds: int, error_kind: str) -> None:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM service_breakers WHERE service_id = ? AND scope = ?", (service_id, scope))
        row = cursor.fetchone()
        failures = int(row["consecutive_failures"]) + 1 if row else 1
        opened_at = row["opened_at"] if row else None
        cooldown_until = row["cooldown_until"] if row else None
        if failures >= threshold:
            opened_at = now_dt.isoformat().replace("+00:00", "Z")
            cooldown_until = (now_dt + timedelta(seconds=cooldown_seconds)).isoformat().replace("+00:00", "Z")
        if row:
            cursor.execute(
                """
                UPDATE service_breakers SET consecutive_failures = ?, opened_at = ?, cooldown_until = ?, last_error_kind = ?, updated_at = ?
                WHERE service_id = ? AND scope = ?
                """,
                (failures, opened_at, cooldown_until, error_kind, now_dt.isoformat().replace("+00:00", "Z"), service_id, scope),
            )
        else:
            cursor.execute(
                """
                INSERT INTO service_breakers (service_id, scope, consecutive_failures, opened_at, cooldown_until, last_error_kind, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (service_id, scope, failures, opened_at, cooldown_until, error_kind, now_dt.isoformat().replace("+00:00", "Z")),
            )
        conn.commit()
    finally:
        conn.close()


def reset_service_breaker(service_id: str, scope: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO service_breakers (service_id, scope, consecutive_failures, opened_at, cooldown_until, last_error_kind, updated_at)
            VALUES (?, ?, 0, NULL, NULL, NULL, ?)
            ON CONFLICT(service_id, scope) DO UPDATE SET consecutive_failures=0, opened_at=NULL, cooldown_until=NULL, last_error_kind=NULL, updated_at=excluded.updated_at
            """,
            (service_id, scope, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def list_intakes(limit: int = 50, tenant: Optional[str] = None, confidence: Optional[str] = None, search: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        conditions = []
        params: List[Any] = []
        if tenant:
            conditions.append("tenant_id = ?")
            params.append(tenant)
        if confidence:
            conditions.append("identity_confidence = ?")
            params.append(confidence)
        if search:
            conditions.append("(subject_raw LIKE ? OR body_raw LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        cursor.execute(
            f"""
            SELECT * FROM intakes
            {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params + [max(1, limit)],
        )
        rows = cursor.fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_intake(intake_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM intakes WHERE intake_id = ?", (intake_id,))
        row = cursor.fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def list_evidence_for_intake(intake_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT evidence_id, tool_name, ran_at, status, summary_external, summary_internal, redaction_level, result_hash, params_json, replays_evidence_id, time_bucket, params_hash
            FROM evidence_runs
            WHERE intake_id = ?
            ORDER BY ran_at DESC
            LIMIT ?
            """,
            (intake_id, max(1, limit)),
        )
        rows = cursor.fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def list_handoffs_for_intake(intake_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM handoff_packs
            WHERE intake_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (intake_id, max(1, limit)),
        )
        rows = cursor.fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def update_intake_status(intake_id: str, status: str, resolution_note: Optional[str] = None) -> None:
    conn = get_connection()
    try:
        resolved_at = _now_iso() if status == "resolved" else None
        conn.execute(
            """
            UPDATE intakes SET status = ?, resolution_note = COALESCE(?, resolution_note), resolved_at = COALESCE(?, resolved_at), updated_at = ?
            WHERE intake_id = ?
            """,
            (status, resolution_note, resolved_at, _now_iso(), intake_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_intake_request_info(intake_id: str, customer_request_id: Optional[str], error_code: Optional[str]) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE intakes SET customer_request_id = COALESCE(?, customer_request_id), error_code = COALESCE(?, error_code), updated_at = ?
            WHERE intake_id = ?
            """,
            (customer_request_id, error_code, _now_iso(), intake_id),
        )
        conn.commit()
    finally:
        conn.close()


def acknowledge_intake(intake_id: str, acknowledged_by: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE intakes SET acknowledged_at = ?, acknowledged_by = ?, updated_at = ?
            WHERE intake_id = ?
            """,
            (_now_iso(), acknowledged_by, _now_iso(), intake_id),
        )
        conn.commit()
    finally:
        conn.close()


def _hash_api_key(api_key: str) -> str:
    return hashlib.sha256((api_key or "").encode("utf-8")).hexdigest()


def log_replay_attempt(
    *,
    api_key: str,
    evidence_id: str,
    new_evidence_id: Optional[str],
    result: str,
    reason: str,
    remote_ip: Optional[str],
    user_agent: Optional[str],
) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO replay_audit (id, api_key_hash, evidence_id, new_evidence_id, ts, result, reason, remote_ip, user_agent)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                _hash_api_key(api_key),
                evidence_id,
                new_evidence_id,
                _now_iso(),
                result,
                reason,
                remote_ip or "",
                user_agent or "",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def count_replays_for_key(api_key: str, window_seconds: int) -> int:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*) AS c FROM replay_audit
            WHERE api_key_hash = ? AND ts >= ?
            """,
            (_hash_api_key(api_key), (datetime.now(timezone.utc) - timedelta(seconds=window_seconds)).isoformat().replace("+00:00", "Z")),
        )
        row = cursor.fetchone()
        return int(row["c"] if row else 0)
    finally:
        conn.close()


def count_replays_for_evidence(evidence_id: str, window_seconds: int) -> int:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*) AS c FROM replay_audit
            WHERE evidence_id = ? AND ts >= ?
            """,
            (evidence_id, (datetime.now(timezone.utc) - timedelta(seconds=window_seconds)).isoformat().replace("+00:00", "Z")),
        )
        row = cursor.fetchone()
        return int(row["c"] if row else 0)
    finally:
        conn.close()


# Ensure the schema exists when the module is imported for the first time.
init_db()
