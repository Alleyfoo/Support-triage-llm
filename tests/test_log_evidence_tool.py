from app.validation import validate_payload
from tools import registry


def test_log_evidence_detects_incident_window():
    params = {
        "service": "api",
        "query_type": "errors",
        "time_window": {"start": "2025-05-01T10:40:00Z", "end": "2025-05-01T11:00:00Z"},
    }
    bundle = registry.run_tool("log_evidence", params)
    validate_payload(bundle, "evidence_bundle.schema.json")
    assert bundle["observed_incident"] is True
    assert bundle["incident_window"]["start"] == "2025-05-01T10:42:10Z"
    assert bundle["incident_window"]["end"] == "2025-05-01T10:58:00Z"
    assert bundle["summary_counts"]["errors"] >= 3


def test_log_evidence_handles_clean_window():
    params = {
        "service": "api",
        "query_type": "errors",
        "time_window": {"start": "2025-05-01T11:30:00Z", "end": "2025-05-01T12:05:00Z"},
    }
    bundle = registry.run_tool("log_evidence", params)
    validate_payload(bundle, "evidence_bundle.schema.json")
    assert bundle["observed_incident"] is False
    assert bundle["summary_counts"]["errors"] == 0


def test_log_evidence_redacts_sensitive_patterns():
    params = {
        "service": "api",
        "query_type": "errors",
        "time_window": {"start": "2025-05-01T10:40:00Z", "end": "2025-05-01T11:00:00Z"},
    }
    bundle = registry.run_tool("log_evidence", params)
    for event in bundle["events"]:
        assert "Authorization" not in event["detail"]
        assert "@" not in event["detail"]
        assert len(event["detail"]) <= 200


def test_log_evidence_defaults_service_from_tenant():
    params = {
        "tenant": "api",
        "query_type": "errors",
        "time_window": {"start": "2025-05-01T10:40:00Z", "end": "2025-05-01T11:00:00Z"},
    }
    bundle = registry.run_tool("log_evidence", params)
    validate_payload(bundle, "evidence_bundle.schema.json")
    assert bundle["metadata"]["log_entry_count"] > 0
