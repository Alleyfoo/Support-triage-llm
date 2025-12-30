from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from tools import migrate_queue_chat


def _write_email_queue(path: Path, rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    with pd.ExcelWriter(path, engine="openpyxl", mode="w") as writer:
        df.to_excel(writer, index=False)


def _read_queue(path: Path) -> pd.DataFrame:
    return pd.read_excel(path)


def test_migrate_queue_converts_email_rows(tmp_path):
    source = tmp_path / "email_queue.xlsx"
    dest = tmp_path / "chat_queue.xlsx"
    rows = [
        {
            "id": "email-1",
            "customer": "customer@example.com",
            "body": "Hello",
            "raw_body": "Hello",
            "status": "done",
            "reply": "Thanks for reaching out!",
            "score": 1.0,
            "expected_keys": "[\"founded_year\"]",
            "matched": "[\"founded_year\"]",
            "missing": "[]",
            "answers": json.dumps({"founded_year": "1990"}),
            "latency_seconds": 2.5,
            "ingest_signature": "sig-123",
        }
    ]
    _write_email_queue(source, rows)

    migrate_queue_chat.migrate_queue(source, dest, overwrite=True)

    df = _read_queue(dest)
    assert df.loc[0, "status"] == "responded"
    assert df.loc[0, "delivery_status"] == "pending"
    payload = json.loads(df.loc[0, "response_payload"])
    assert payload["content"] == "Thanks for reaching out!"
    tags = json.loads(df.loc[0, "conversation_tags"])
    assert tags == ["founded_year"]
    metadata = json.loads(df.loc[0, "response_metadata"])
    assert metadata["migrated_from"] == "email_queue"
    assert metadata["answers"]["founded_year"] == "1990"


def test_migrate_queue_requires_overwrite(tmp_path):
    source = tmp_path / "email_queue.xlsx"
    dest = tmp_path / "chat_queue.xlsx"
    _write_email_queue(source, [])
    _write_email_queue(dest, [])

    try:
        migrate_queue_chat.migrate_queue(source, dest)
    except SystemExit as exc:
        assert "Use --overwrite" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("Expected SystemExit when destination exists without overwrite")
