from __future__ import annotations

from pathlib import Path

import pandas as pd

from app import server


def test_chat_enqueue_writes_queue(tmp_path, monkeypatch):
    queue_path = tmp_path / "queue.xlsx"
    monkeypatch.setattr(server, "CHAT_QUEUE_PATH", queue_path)

    response = server.enqueue_chat(
        {
            "conversation_id": "test-conv",
            "text": "Hello, need help",
            "end_user_handle": "tmp-user",
            "channel": "web_chat",
        }
    )
    assert response == {"enqueued": 1}

    df = pd.read_excel(queue_path)
    assert df.loc[0, "conversation_id"] == "test-conv"
    assert df.loc[0, "status"].lower() == "queued"


def test_chat_enqueue_ignores_empty(tmp_path, monkeypatch):
    queue_path = tmp_path / "queue.xlsx"
    monkeypatch.setattr(server, "CHAT_QUEUE_PATH", queue_path)

    response = server.enqueue_chat({"conversation_id": "c1", "text": "   "})
    assert response == {"enqueued": 0}
    assert not queue_path.exists()
