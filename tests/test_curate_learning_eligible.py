import json
from pathlib import Path

from app import queue_db
from tools import curate_golden_dataset


def test_curate_uses_learning_eligible_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(queue_db, "DB_PATH", tmp_path / "queue.db")
    queue_db.init_db()

    conn = queue_db.get_connection()
    cur = conn.cursor()
    base = {
        "status": "responded",
        "closed_loop_at": "2026-03-01T00:00:00Z",
        "edit_distance": 0.01,
        "redacted_payload": "Issue details",
        "triage_json": json.dumps({"case_type": "email_delivery"}),
        "draft_customer_reply_subject": "s",
        "draft_customer_reply_body": "b",
        "sent_body": "sent",
    }
    cur.execute(
        """
        INSERT INTO queue (status, closed_loop_at, edit_distance, redacted_payload, triage_json,
                           draft_customer_reply_subject, draft_customer_reply_body, sent_body, learning_eligible)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            base["status"],
            base["closed_loop_at"],
            base["edit_distance"],
            base["redacted_payload"],
            base["triage_json"],
            base["draft_customer_reply_subject"],
            base["draft_customer_reply_body"],
            base["sent_body"],
            0,
        ),
    )
    cur.execute(
        """
        INSERT INTO queue (status, closed_loop_at, edit_distance, redacted_payload, triage_json,
                           draft_customer_reply_subject, draft_customer_reply_body, sent_body, learning_eligible)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            base["status"],
            base["closed_loop_at"],
            base["edit_distance"],
            base["redacted_payload"],
            base["triage_json"],
            base["draft_customer_reply_subject"],
            base["draft_customer_reply_body"],
            base["sent_body"],
            1,
        ),
    )
    conn.commit()
    conn.close()

    out = tmp_path / "golden.jsonl"
    perfect, correction, rejection = curate_golden_dataset.curate(out, limit=50, include_rejections=False)
    assert (perfect, correction, rejection) == (1, 0, 0)
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
