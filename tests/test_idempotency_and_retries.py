from pathlib import Path

import pytest

from app import config, queue_db
from tools import triage_worker


def _use_temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "queue.db"
    monkeypatch.setattr(queue_db, "DB_PATH", db_path)
    queue_db.init_db()


def test_insert_message_dedupes(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)

    row_id, created = queue_db.insert_message({"text": "hello", "end_user_handle": "tenant-a"})
    assert created is True
    second_id, created_again = queue_db.insert_message({"text": "hello", "end_user_handle": "tenant-a"})

    assert created_again is False
    assert second_id == row_id

    rows = queue_db.fetch_queue()
    assert len(rows) == 1
    assert rows[0]["retry_count"] == 0


def test_worker_backoff_and_dead_letter(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "MAX_RETRIES", 1)
    monkeypatch.setattr(config, "RETRY_BASE_SECONDS", 1)
    monkeypatch.setattr(config, "RETRY_MAX_SECONDS", 30)

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(triage_worker, "triage", boom)

    row_id, _ = queue_db.insert_message({"text": "fail please", "end_user_handle": "tenant"})
    assert triage_worker.process_once("tester") is True

    rows = {row["id"]: row for row in queue_db.fetch_queue()}
    first = rows[row_id]
    assert first["status"] == "queued"
    assert first["retry_count"] == 1
    assert first.get("available_at")

    # Should skip processing because available_at is in the future
    assert triage_worker.process_once("tester") is False

    # Force immediate dead-letter on the next message
    monkeypatch.setattr(config, "MAX_RETRIES", 0)
    row_id2, _ = queue_db.insert_message({"text": "deadletter", "end_user_handle": "tenant"})
    assert triage_worker.process_once("tester") is True

    rows = {row["id"]: row for row in queue_db.fetch_queue()}
    second = rows[row_id2]
    assert second["status"] == "dead_letter"
    assert second["retry_count"] >= 1
