import os
import socket
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.security import APIKeyHeader

from . import config, queue_db
from .pipeline import run_pipeline
from .schemas import ChatEnqueueRequest, EmailRequest, EmailResponse
from tools import chat_ingest

app = FastAPI()
MODEL_READY = True
CHAT_QUEUE_PATH = Path("data/email_queue.xlsx")
USE_DB_QUEUE = os.environ.get("USE_DB_QUEUE", "true").lower() == "true"
API_KEY_NAME = "X-API-KEY"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)


def _get_api_key(api_key_header: str = Security(api_key_header)) -> str:
    if not config.REQUIRE_API_KEY:
        return api_key_header or ""
    expected = config.INGEST_API_KEY
    if not expected:
        raise HTTPException(status_code=503, detail="API key not configured")
    if api_key_header != expected:
        raise HTTPException(status_code=403, detail="Invalid or missing API Key")
    return api_key_header


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
        "text": payload.text,
        "end_user_handle": payload.end_user_handle or "api-user",
        "channel": payload.channel or "web_chat",
        "message_id": payload.message_id or "",
        "raw_payload": payload.raw_payload or "",
    }
    if USE_DB_QUEUE:
        queue_id = queue_db.insert_message(message)
        return {"enqueued": 1, "queue_id": queue_id}

    count = chat_ingest.ingest_messages(CHAT_QUEUE_PATH, [message])
    return {"enqueued": count}
