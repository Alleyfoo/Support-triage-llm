import importlib
import json
import os
from pathlib import Path

import pytest
import threading
import time


def _setup_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "queue.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    import sys
    sys.modules.pop("app.config", None)
    sys.modules.pop("app.queue_db", None)
    from app import config as cfg
    cfg.DB_PATH = str(db_path)
    from app import queue_db as qd
    qd.DB_PATH = db_path
    qd.init_db()
    return qd


def _reload_evidence_runner():
    import sys
    sys.modules.pop("tools.evidence_runner", None)
    from tools import evidence_runner as er
    return er


def test_intake_immutability(monkeypatch, tmp_path):
    queue_db = _setup_db(tmp_path, monkeypatch)
    intake_id = queue_db.insert_intake(
        received_at="2025-01-01T00:00:00Z",
        channel="email",
        from_address="user@example.com",
        claimed_domain=None,
        subject_raw="Subject A",
        body_raw="Body A",
    )
    queue_db.update_intake_tenant(intake_id, "tenant-1", "high")

    conn = queue_db.get_connection()
    try:
        row = conn.execute("SELECT subject_raw, body_raw, tenant_id, identity_confidence FROM intakes WHERE intake_id = ?", (intake_id,)).fetchone()
    finally:
        conn.close()
    assert row["subject_raw"] == "Subject A"
    assert row["body_raw"] == "Body A"
    assert row["tenant_id"] == "tenant-1"
    assert row["identity_confidence"] == "high"


def test_evidence_replayability_and_redaction(monkeypatch, tmp_path):
    queue_db = _setup_db(tmp_path, monkeypatch)
    evidence_runner = _reload_evidence_runner()
    intake_id = queue_db.insert_intake(
        received_at="2025-05-01T10:00:00Z",
        channel="email",
        from_address="ops@example.com",
        claimed_domain=None,
        subject_raw="Down",
        body_raw="API down",
    )
    params = {
        "service": "api",
        "query_type": "errors",
        "time_window": {"start": "2025-05-01T10:40:00Z", "end": "2025-05-01T11:00:00Z"},
    }
    evidence1, bundle1 = evidence_runner.run_tool_with_evidence(intake_id, "log_evidence", params)
    evidence2, bundle2 = evidence_runner.run_tool_with_evidence(intake_id, "log_evidence", params)
    assert evidence1["evidence_id"] == evidence2["evidence_id"]
    assert "@" not in evidence1["summary_external"]
    assert bundle1["metadata"]["evidence_id"] == evidence1["evidence_id"]
    assert bundle2["metadata"]["evidence_id"] == evidence1["evidence_id"]


def test_handoff_pack_contains_refs(monkeypatch, tmp_path):
    queue_db = _setup_db(tmp_path, monkeypatch)
    evidence_runner = _reload_evidence_runner()
    intake_id = queue_db.insert_intake(
        received_at="2025-05-01T10:00:00Z",
        channel="email",
        from_address="ops@example.com",
        claimed_domain=None,
        subject_raw="Down",
        body_raw="API down",
    )
    params = {
        "service": "api",
        "query_type": "errors",
        "time_window": {"start": "2025-05-01T10:40:00Z", "end": "2025-05-01T11:00:00Z"},
    }
    evidence_record, _ = evidence_runner.run_tool_with_evidence(intake_id, "log_evidence", params)
    payload = {
        "intake_id": intake_id,
        "evidence_refs": [{"evidence_id": evidence_record["evidence_id"], "tool": "log_evidence"}],
    }
    handoff_id = queue_db.create_handoff_pack(intake_id=intake_id, tier=3, payload_json=payload)
    conn = queue_db.get_connection()
    try:
        row = conn.execute("SELECT payload_json FROM handoff_packs WHERE handoff_id = ?", (handoff_id,)).fetchone()
    finally:
        conn.close()
    parsed = json.loads(row["payload_json"])
    assert parsed["evidence_refs"][0]["evidence_id"] == evidence_record["evidence_id"]
    assert "raw" not in row["payload_json"].lower()


def test_evidence_external_summary_redaction(monkeypatch, tmp_path):
    queue_db = _setup_db(tmp_path, monkeypatch)
    evidence_runner = _reload_evidence_runner()
    intake_id = queue_db.insert_intake(
        received_at="2025-05-01T10:00:00Z",
        channel="email",
        from_address="ops@example.com",
        claimed_domain=None,
        subject_raw="Down",
        body_raw="API down",
    )
    params = {
        "service": "api",
        "query_type": "errors",
        "time_window": {"start": "2025-05-01T10:40:00Z", "end": "2025-05-01T11:00:00Z"},
    }
    evidence_record, _ = evidence_runner.run_tool_with_evidence(intake_id, "log_evidence", params)
    summary = evidence_record["summary_external"]
    assert "Authorization" not in summary
    assert "@" not in summary
    assert "internal" not in summary.lower()


def test_evidence_concurrency_cache(monkeypatch, tmp_path):
    queue_db = _setup_db(tmp_path, monkeypatch)
    evidence_runner = _reload_evidence_runner()
    intake_id = queue_db.insert_intake(
        received_at="2025-05-01T10:00:00Z",
        channel="email",
        from_address="ops@example.com",
        claimed_domain=None,
        subject_raw="Down",
        body_raw="API down",
    )
    params = {
        "service": "api",
        "query_type": "errors",
        "time_window": {"start": "2025-05-01T10:40:00Z", "end": "2025-05-01T11:00:00Z"},
    }

    results = []

    def worker():
        rec, _ = evidence_runner.run_tool_with_evidence(intake_id, "log_evidence", params)
        results.append(rec["evidence_id"])

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(set(results)) == 1


def test_replay_evidence_creates_lineage(monkeypatch, tmp_path):
    queue_db = _setup_db(tmp_path, monkeypatch)
    evidence_runner = _reload_evidence_runner()
    intake_id = queue_db.insert_intake(
        received_at="2025-05-01T10:00:00Z",
        channel="email",
        from_address="ops@example.com",
        claimed_domain=None,
        subject_raw="Down",
        body_raw="API down",
    )
    params = {
        "service": "api",
        "query_type": "errors",
        "time_window": {"start": "2025-05-01T10:40:00Z", "end": "2025-05-01T11:00:00Z"},
    }
    evidence_record, _ = evidence_runner.run_tool_with_evidence(intake_id, "log_evidence", params)
    replay_record, _ = evidence_runner.replay_evidence(evidence_record["evidence_id"])
    assert replay_record["replays_evidence_id"] == evidence_record["evidence_id"]
    assert replay_record["evidence_id"] != evidence_record["evidence_id"]


def test_draft_does_not_leak_internal(monkeypatch, tmp_path):
    queue_db = _setup_db(tmp_path, monkeypatch)
    intake_id = queue_db.insert_intake(
        received_at="2025-05-01T10:00:00Z",
        channel="email",
        from_address="ops@example.com",
        claimed_domain=None,
        subject_raw="Down",
        body_raw="API down",
    )
    draft = {"subject": "Update", "body": "Initial"}
    poison = "INTERNAL_SECRET_SHOULD_NEVER_APPEAR"
    log_bundle = {
        "evidence_type": "logs",
        "observed_incident": True,
        "incident_window": {"start": "2025-05-01T10:40:00Z", "end": "2025-05-01T10:58:00Z"},
        "metadata": {"query_type": "errors", "summary_external": "We observed elevated errors", "checked_at": "2025-05-01T10:59:00Z"},
        "events": [{"detail": poison}],
    }
    from tools import triage_worker

    updated = triage_worker._append_log_statement(draft, [log_bundle], identity_confidence="low")
    assert poison not in updated["body"]
    assert "organization" in updated["body"]
