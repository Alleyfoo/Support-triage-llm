from __future__ import annotations

from pathlib import Path

import pandas as pd

from tools import chat_ingest
from tools import chat_worker


def _load_queue(path: Path) -> pd.DataFrame:
    return pd.read_excel(path)


def test_ingest_writes_rows(tmp_path):
    queue_path = tmp_path / "queue.xlsx"
    messages = [
        {
            "conversation_id": "conv-demo",
            "text": "Hello there",
            "end_user_handle": "demo-user",
            "channel": "web_chat",
        },
        {
            "conversation_id": "conv-demo",
            "text": "Can you help me?",
            "end_user_handle": "demo-user",
        },
    ]
    inserted = chat_ingest.ingest_messages(queue_path, messages)
    assert inserted == 2

    df = _load_queue(queue_path)
    assert df.shape[0] == 2
    assert set(df["conversation_id"]) == {"conv-demo"}
    assert (df["status"].astype(str) == "queued").all()


def test_ingest_skips_empty_messages(tmp_path):
    queue_path = tmp_path / "queue.xlsx"
    messages = [
        {"conversation_id": "c1", "text": ""},
        {"conversation_id": "c2", "text": "   "},
    ]
    inserted = chat_ingest.ingest_messages(queue_path, messages)
    assert inserted == 0
    assert not queue_path.exists()
