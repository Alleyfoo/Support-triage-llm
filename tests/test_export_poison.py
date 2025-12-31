from fastapi.testclient import TestClient
import importlib


def test_external_export_redacts_internal(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "queue.db"))
    import app.queue_db as queue_db
    importlib.reload(queue_db)
    from app.server import app  # import after DB_PATH set
    client = TestClient(app)

    intake_id = queue_db.insert_intake(
        received_at="2025-05-01T10:00:00Z",
        channel="email",
        from_address="ops@example.com",
        claimed_domain=None,
        subject_raw="Export test",
        body_raw="Body",
    )
    poison = "INTERNAL_SECRET_SHOULD_NEVER_APPEAR"
    params = {"service": "api", "query_type": "errors", "time_window": {"start": "2025-05-01T10:00:00Z", "end": "2025-05-01T11:00:00Z"}}
    queue_db.record_evidence_run(
        intake_id=intake_id,
        tool_name="log_evidence",
        params=params,
        result={"metadata": {"status": "down"}, "events": []},
        summary_external="external summary",
        summary_internal="internal summary",
        status="ok",
        redaction_level="internal",
        ttl_seconds=None,
        error_message=None,
        replays_evidence_id=None,
    )

    resp = client.get(f"/intakes/{intake_id}/export", params={"mode": "external"})
    assert resp.status_code == 200
    payload = resp.json()
    dumped = str(payload)
    assert poison not in dumped
    forbidden_tokens = ["Authorization", "Bearer", ".internal", ".corp"]
    for tok in forbidden_tokens:
        assert tok not in dumped
    assert payload["export_version"] == 1
