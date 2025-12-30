from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from app.chat_service import ChatService
from tools import chat_worker, chat_dispatcher


def _write_queue(path: Path, rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    df = chat_worker.ensure_chat_columns(df)
    with pd.ExcelWriter(path, engine="openpyxl", mode="w") as writer:
        df.to_excel(writer, index=False)


def _load_queue(path: Path) -> pd.DataFrame:
    return pd.read_excel(path)


def test_chat_worker_answers_known_fact(tmp_path):
    queue_path = tmp_path / "queue.xlsx"
    rows = [
        {
            "conversation_id": "conv-1",
            "payload": "What year were you founded?",
            "status": "queued",
            "end_user_handle": "customer-42",
        }
    ]
    _write_queue(queue_path, rows)

    service = ChatService()
    processed = chat_worker.process_once(queue_path, processor_id="test-worker", chat_service=service)
    assert processed is True

    df = _load_queue(queue_path)
    assert df.loc[0, "status"] == "responded"
    payload = json.loads(df.loc[0, "response_payload"])
    assert "1990" in payload["content"]
    assert df.loc[0, "delivery_status"] == "pending"


def test_chat_dispatcher_marks_row_delivered(tmp_path):
    queue_path = tmp_path / "queue.xlsx"
    log_path = tmp_path / "transcript.jsonl"
    rows = [
        {
            "conversation_id": "conv-1",
            "payload": "Tell me about the loyalty program",
            "status": "queued",
            "end_user_handle": "customer-17",
        }
    ]
    _write_queue(queue_path, rows)

    chat_worker.process_once(queue_path, processor_id="test-worker")
    dispatched = chat_dispatcher.dispatch_once(
        queue_path,
        dispatcher_id="test-dispatcher",
        adapter="web-demo",
        adapter_target=str(log_path),
    )
    assert dispatched == 1

    df = _load_queue(queue_path)
    assert df.loc[0, "status"] == "delivered"
    assert df.loc[0, "delivery_status"] == "sent"
    metadata = json.loads(df.loc[0, "response_metadata"])
    assert metadata["dispatcher_id"] == "test-dispatcher"
    assert metadata["delivery_adapter"] == "web-demo"

    transcript = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert transcript
    last_entry = json.loads(transcript[-1])
    assert last_entry["conversation_id"] == "conv-1"
    assert last_entry["response"]["type"] == "text"


def test_chat_worker_returns_false_when_queue_empty(tmp_path):
    queue_path = tmp_path / "queue.xlsx"
    _write_queue(queue_path, [])

    processed = chat_worker.process_once(queue_path, processor_id="test-worker")
    assert processed is False

def test_chat_worker_emits_clarify(tmp_path):
    queue_path = tmp_path / "queue.xlsx"
    rows = [
        {
            "conversation_id": "conv-clarify",
            "payload": "Hi",
            "status": "queued",
        }
    ]
    _write_queue(queue_path, rows)

    chat_worker.process_once(queue_path, processor_id="clarify-worker")
    df = _load_queue(queue_path)
    payload = json.loads(df.loc[0, "response_payload"])
    assert payload["decision"] == "clarify"
    assert df.loc[0, "status"] == "responded"


def test_chat_worker_emits_handoff(tmp_path):
    queue_path = tmp_path / "queue.xlsx"
    rows = [
        {
            "conversation_id": "conv-handoff",
            "payload": "Can I speak to a human agent please?",
            "status": "queued",
        }
    ]
    _write_queue(queue_path, rows)

    chat_worker.process_once(queue_path, processor_id="handoff-worker")
    df = _load_queue(queue_path)
    payload = json.loads(df.loc[0, "response_payload"])
    assert payload["decision"] == "handoff"
    assert df.loc[0, "status"] == "handoff"
    assert df.loc[0, "delivery_status"] == "blocked"
