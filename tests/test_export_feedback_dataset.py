import importlib
import json
import os
from pathlib import Path

import pytest


def _seed_row(tmp_path: Path, include_email: bool = False):
    os.environ["DB_PATH"] = str(tmp_path / "queue.sqlite")
    from app import config  # type: ignore
    importlib.reload(config)
    from app import queue_db as qdb  # type: ignore

    importlib.reload(qdb)
    qdb.init_db()
    conn = qdb.get_connection()
    cur = conn.cursor()
    body_text = "All good" if not include_email else "Contact john@acme.com"
    triage = {"case_type": "email_delivery", "severity": "high"}
    report = {"classification": {"failure_stage": "recipient"}, "_meta": {}}
    redacted_payload = "[REDACTED_EMAIL]@example.com" if not include_email else "john@acme.com"
    cur.execute(
        """
        INSERT INTO queue
        (status, case_id, triage_json, final_report_json, redacted_payload,
         review_action, review_final_subject, review_final_body, draft_customer_reply_subject, draft_customer_reply_body)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "triaged",
            "case-export",
            json.dumps(triage),
            json.dumps(report),
            redacted_payload,
            "approved",
            "Final subj",
            body_text,
            "Draft subj",
            body_text,
        ),
    )
    conn.commit()
    conn.close()
    return qdb


def test_export_feedback_dataset_success(tmp_path, monkeypatch):
    qdb = _seed_row(tmp_path, include_email=False)
    from tools import export_feedback_dataset

    importlib.reload(export_feedback_dataset)
    export_feedback_dataset.queue_db = qdb
    out_path = tmp_path / "export.jsonl"
    rc = export_feedback_dataset.export_dataset(tmp_path / "queue.sqlite", out_path, allow_dataset_export=True)
    assert rc == 0
    assert out_path.exists()
    content = out_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(content) == 1


def test_export_feedback_dataset_blocks_unredacted(tmp_path):
    qdb = _seed_row(tmp_path, include_email=True)
    from tools import export_feedback_dataset

    importlib.reload(export_feedback_dataset)
    export_feedback_dataset.queue_db = qdb
    out_path = tmp_path / "export.jsonl"
    with pytest.raises(RuntimeError):
        export_feedback_dataset.export_dataset(tmp_path / "queue.sqlite", out_path, allow_dataset_export=True)
