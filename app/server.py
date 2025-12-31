import os
import socket
import hashlib
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Security, Request
from fastapi.security import APIKeyHeader

from . import config, queue_db
from .pipeline import run_pipeline
from .schemas import ChatEnqueueRequest, EmailRequest, EmailResponse, TriageRequest
from .triage_service import triage
from .metrics_api import router as metrics_router
from tools import chat_ingest, evidence_runner

app = FastAPI()
MODEL_READY = True
CHAT_QUEUE_PATH = Path("data/email_queue.xlsx")
USE_DB_QUEUE = os.environ.get("USE_DB_QUEUE", "true").lower() == "true"
API_KEY_NAME = "X-API-KEY"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)
REPLAY_LIMIT_PER_MIN = 30
REPLAY_LIMIT_PER_EVIDENCE_PER_HOUR = 10
ADMIN_API_KEY_HASHES = set((os.environ.get("ADMIN_API_KEY_HASHES") or "").split(",") if os.environ.get("ADMIN_API_KEY_HASHES") else [])
T3_API_KEY_HASHES = set((os.environ.get("T3_API_KEY_HASHES") or "").split(",") if os.environ.get("T3_API_KEY_HASHES") else [])


def _get_api_key(api_key_header: str = Security(api_key_header)) -> str:
    if not config.REQUIRE_API_KEY:
        return api_key_header or ""
    expected = config.INGEST_API_KEY
    if not expected:
        raise HTTPException(status_code=503, detail="API key not configured")
    if api_key_header != expected:
        raise HTTPException(status_code=403, detail="Invalid or missing API Key")
    return api_key_header


def _api_role(api_key: str) -> str:
    if not api_key:
        return "default"
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    if digest in ADMIN_API_KEY_HASHES:
        return "admin"
    if digest in T3_API_KEY_HASHES:
        return "t3"
    return "default"


def _check_db() -> bool:
    try:
        conn = queue_db.get_connection()
        conn.execute("SELECT 1")
        conn.close()
        return True
    except Exception:
        return False


def _check_ollama() -> bool:
    parsed = urlparse(config.OLLAMA_HOST)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 11434
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


@app.get("/healthz")
def healthz() -> Dict[str, Any]:
    db_ok = _check_db()
    ollama_ok = _check_ollama()
    status_code = 200 if (MODEL_READY and db_ok and ollama_ok) else 503
    if status_code != 200:
        raise HTTPException(
            status_code=status_code,
            detail={"model_loaded": MODEL_READY, "db": db_ok, "ollama": ollama_ok},
        )
    return {"status": "ok", "model_loaded": MODEL_READY, "db": db_ok, "ollama": ollama_ok}


@app.post("/reply", response_model=EmailResponse)
def reply(req: EmailRequest) -> EmailResponse:
    metadata: Dict[str, Any] = {}
    if req.expected_keys:
        metadata["expected_keys"] = req.expected_keys
    if req.customer_email:
        metadata["customer_email"] = req.customer_email
    if req.subject:
        metadata["subject"] = req.subject
    result = run_pipeline(req.email, metadata=metadata or None)
    return EmailResponse(**result)


@app.post("/chat/enqueue", dependencies=[Depends(_get_api_key)])
def enqueue_chat(payload: ChatEnqueueRequest) -> Dict[str, int]:
    message = {
        "conversation_id": payload.conversation_id or "api-web",
        "text": (payload.text or "").strip(),
        "end_user_handle": payload.end_user_handle or "api-user",
        "channel": payload.channel or "web_chat",
        "message_id": payload.message_id or "",
        "raw_payload": payload.raw_payload or "",
        "case_id": payload.message_id or payload.conversation_id or "",
    }
    if not message["text"]:
        return {"enqueued": 0, "queue_id": None, "deduped": False}
    if USE_DB_QUEUE:
        queue_id, created = queue_db.insert_message(message)
        return {"enqueued": 1 if created else 0, "queue_id": queue_id, "deduped": not created}

    count = chat_ingest.ingest_messages(CHAT_QUEUE_PATH, [message])
    return {"enqueued": count}


@app.post("/triage/run")
def triage_run(req: TriageRequest) -> Dict[str, object]:
    result = triage(
        req.text,
        metadata={"tenant": req.tenant, "source": req.source, "received_at": req.received_at},
    )
    result["_case_id"] = req.source or ""
    return result


@app.post("/triage/enqueue", dependencies=[Depends(_get_api_key)])
def triage_enqueue(req: TriageRequest) -> Dict[str, int]:
    message = {
        "conversation_id": req.source or "triage",
        "text": req.text,
        "end_user_handle": req.tenant or "",
        "channel": "triage",
        "message_id": "",
        "case_id": req.source or "",
        "raw_payload": "",
        "ingest_signature": "triage-api",
    }
    queue_id, created = queue_db.insert_message(message)
    return {"enqueued": 1 if created else 0, "queue_id": queue_id, "deduped": not created}


@app.post("/evidence/{evidence_id}/replay", dependencies=[Depends(_get_api_key)])
def replay_evidence(evidence_id: str, request: Request, api_key: str = Depends(_get_api_key), force: bool = False) -> Dict[str, Any]:
    role = _api_role(api_key)
    if force and role != "admin":
        raise HTTPException(status_code=403, detail="Force refresh requires admin scope")
    # Rate limits
    if queue_db.count_replays_for_key(api_key or "", 60) >= REPLAY_LIMIT_PER_MIN:
        queue_db.log_replay_attempt(api_key=api_key or "", evidence_id=evidence_id, new_evidence_id=None, result="blocked", reason="rate_limited", remote_ip=request.client.host if request.client else "", user_agent=request.headers.get("user-agent"))
        raise HTTPException(status_code=429, detail="Replay rate limit exceeded")
    if queue_db.count_replays_for_evidence(evidence_id, 3600) >= REPLAY_LIMIT_PER_EVIDENCE_PER_HOUR:
        queue_db.log_replay_attempt(api_key=api_key or "", evidence_id=evidence_id, new_evidence_id=None, result="blocked", reason="evidence_cap", remote_ip=request.client.host if request.client else "", user_agent=request.headers.get("user-agent"))
        raise HTTPException(status_code=429, detail="Replay cap for this evidence exceeded")

    try:
        record, result = evidence_runner.replay_evidence(evidence_id, force=force)
        queue_db.log_replay_attempt(api_key=api_key or "", evidence_id=evidence_id, new_evidence_id=record.get("evidence_id"), result="ok", reason="ok", remote_ip=request.client.host if request.client else "", user_agent=request.headers.get("user-agent"))
    except Exception as exc:
        queue_db.log_replay_attempt(api_key=api_key or "", evidence_id=evidence_id, new_evidence_id=None, result="blocked", reason=str(exc), remote_ip=request.client.host if request.client else "", user_agent=request.headers.get("user-agent"))
        raise

    return {
        "evidence_id": record.get("evidence_id"),
        "replays_evidence_id": evidence_id,
        "result_hash": record.get("result_hash"),
        "metadata": result.get("metadata"),
        "cache_hit": result.get("metadata", {}).get("cache_hit"),
        "diff": record.get("diff"),
    }


app.include_router(metrics_router)


@app.get("/intakes")
def list_intakes(tenant: str | None = None, confidence: str | None = None, q: str | None = None, limit: int = 50) -> Dict[str, Any]:
    items = queue_db.list_intakes(limit=limit, tenant=tenant, confidence=confidence, search=q)
    return {"intakes": items}


@app.get("/intakes/{intake_id}")
def get_intake(intake_id: str) -> Dict[str, Any]:
    intake = queue_db.get_intake(intake_id)
    if not intake:
        raise HTTPException(status_code=404, detail="intake not found")
    return intake


@app.get("/intakes/{intake_id}/evidence")
def get_intake_evidence(intake_id: str, limit: int = 100) -> Dict[str, Any]:
    evidence = queue_db.list_evidence_for_intake(intake_id, limit=limit)
    return {"evidence": evidence}


@app.get("/intakes/{intake_id}/handoffs")
def get_intake_handoffs(intake_id: str, limit: int = 10) -> Dict[str, Any]:
    handoffs = queue_db.list_handoffs_for_intake(intake_id, limit=limit)
    return {"handoffs": handoffs}


@app.post("/intakes/{intake_id}/status", dependencies=[Depends(_get_api_key)])
def update_intake_status(intake_id: str, payload: Dict[str, Any], api_key: str = Depends(_get_api_key)) -> Dict[str, str]:
    status = payload.get("status")
    if status not in {"new", "investigating", "awaiting_customer", "escalated", "resolved"}:
        raise HTTPException(status_code=400, detail="invalid status")
    resolution_note = payload.get("resolution_note")
    queue_db.update_intake_status(intake_id, status, resolution_note)
    return {"status": "ok"}


@app.post("/intakes/{intake_id}/acknowledge", dependencies=[Depends(_get_api_key)])
def acknowledge_intake(intake_id: str, api_key: str = Depends(_get_api_key)) -> Dict[str, Any]:
    role = _api_role(api_key)
    if role not in {"admin", "t3"}:
        raise HTTPException(status_code=403, detail="insufficient role")
    intake = queue_db.get_intake(intake_id)
    if not intake:
        raise HTTPException(status_code=404, detail="not found")
    if intake.get("status") != "escalated":
        raise HTTPException(status_code=400, detail="cannot acknowledge unless escalated")
    queue_db.acknowledge_intake(intake_id, hashlib.sha256((api_key or '').encode('utf-8')).hexdigest())
    return {"status": "ok", "intake": queue_db.get_intake(intake_id)}


@app.get("/intakes/{intake_id}/export")
def export_intake(intake_id: str, mode: str = "external", api_key: str = Depends(_get_api_key)) -> Dict[str, Any]:
    intake = queue_db.get_intake(intake_id)
    if not intake:
        raise HTTPException(status_code=404, detail="not found")
    evidence = queue_db.list_evidence_for_intake(intake_id, limit=500)
    handoffs = queue_db.list_handoffs_for_intake(intake_id, limit=20)
    if mode not in {"external", "internal"}:
        raise HTTPException(status_code=400, detail="invalid mode")

    envelope = {
        "intake_id": intake.get("intake_id"),
        "received_at": intake.get("received_at"),
        "channel": intake.get("channel"),
        "from_address": intake.get("from_address"),
        "subject": intake.get("subject_raw"),
        "body": intake.get("body_raw"),
        "tenant_id": intake.get("tenant_id"),
        "identity_confidence": intake.get("identity_confidence"),
        "status": intake.get("status"),
        "resolved_at": intake.get("resolved_at"),
        "customer_request_id": intake.get("customer_request_id"),
        "error_code": intake.get("error_code"),
    }
    evidence_payload = []
    for ev in evidence:
        item = {
            "evidence_id": ev.get("evidence_id"),
            "tool_name": ev.get("tool_name"),
            "ran_at": ev.get("ran_at"),
            "summary_external": ev.get("summary_external"),
            "status": ev.get("status"),
            "replays_evidence_id": ev.get("replays_evidence_id"),
            "time_bucket": ev.get("time_bucket"),
            "params_hash": ev.get("params_hash"),
        }
        if mode == "internal":
            item["params_json"] = ev.get("params_json")
            item["summary_internal"] = ev.get("summary_internal")
            item["result_hash"] = ev.get("result_hash")
        evidence_payload.append(item)

    payload = {
        "export_version": 1,
        "envelope": envelope,
        "evidence": evidence_payload,
        "handoffs": handoffs,
    }
    return payload
