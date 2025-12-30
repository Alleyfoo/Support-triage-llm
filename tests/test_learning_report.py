import importlib
import json
import os
from pathlib import Path

import pytest


def _setup_db(tmp_path: Path):
    os.environ["DB_PATH"] = str(tmp_path / "queue.sqlite")
    from app import config  # type: ignore
    importlib.reload(config)
    from app import queue_db as qdb  # type: ignore

    importlib.reload(qdb)
    qdb.init_db()
    conn = qdb.get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO queue
        (status, case_id, triage_json, final_report_json, evidence_sources_run, triage_draft_subject, triage_draft_body,
         draft_customer_reply_subject, draft_customer_reply_body, missing_info_questions, started_at, finished_at,
         reviewed_at, review_action, redacted_payload, review_final_subject, review_final_body, diff_subject_ratio, diff_body_ratio, error_tags, triage_mode, llm_model)
        VALUES
        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "triaged",
            "case-1",
            json.dumps({"case_type": "email_delivery", "severity": "high", "missing_info_questions": ["what time?"], "scope": {"recipient_domains": ["contoso.com"]}}),
            json.dumps({"_meta": {"claim_warnings": ["missing evidence"]}}),
            json.dumps(["fetch_email_events_sample"]),
            "Subj draft",
            "Body draft",
            "Subj draft edited",
            "Body draft edited",
            json.dumps(["what time?"]),
            "2025-01-01T00:00:00Z",
            "2025-01-01T00:01:00Z",
            "2025-01-01T00:02:00Z",
            "approved",
            "Tests happening at 08:00 UTC",
            "Subj draft",
            "Body draft edited",
            0.1,
            0.2,
            json.dumps(["redundant_questions"]),
            "llm",
            "llama3.1:8b",
        ),
    )
    conn.commit()
    conn.close()
    return qdb


def test_learning_report_outputs(tmp_path, monkeypatch):
    qdb = _setup_db(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "queue.sqlite"))

    from tools import learning_report

    importlib.reload(learning_report)
    learning_report.queue_db = qdb
    learning_report.LEARNING_DIR = tmp_path / "learning"

    rc = learning_report.main([])
    assert rc == 0

    metrics_path = learning_report.LEARNING_DIR / "learning_metrics.json"
    assert metrics_path.exists()
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert "redundant_time_questions" in metrics
    assert metrics["claim_warnings_total"] == 1

    csv_path = learning_report.LEARNING_DIR / "learning_rows.csv"
    assert csv_path.exists()
