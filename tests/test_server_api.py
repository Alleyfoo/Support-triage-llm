from __future__ import annotations

from pathlib import Path

import pytest

from app import queue_db, server
from app.schemas import ChatEnqueueRequest


def test_chat_enqueue_writes_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "USE_DB_QUEUE", True)
    monkeypatch.setattr(queue_db, "DB_PATH", tmp_path / "queue.db")
    queue_db.init_db()

    req = ChatEnqueueRequest(conversation_id="test-conv", text="Hello, need help", end_user_handle="tmp-user", channel="web_chat")
    response = server.enqueue_chat(req)
    assert response["enqueued"] == 1
    assert response["deduped"] is False

    duplicate = server.enqueue_chat(req)
    assert duplicate["enqueued"] == 0
    assert duplicate["deduped"] is True

    rows = queue_db.fetch_queue()
    assert len(rows) == 1
    assert rows[0]["conversation_id"] == "test-conv"


def test_chat_enqueue_rejects_empty(monkeypatch):
    req = ChatEnqueueRequest(conversation_id="c1", text="   ")
    resp = server.enqueue_chat(req)
    assert resp["enqueued"] == 0
    assert resp["queue_id"] is None
