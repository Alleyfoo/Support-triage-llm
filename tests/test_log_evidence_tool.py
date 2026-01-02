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
    assert bundle["incident_window"]["start"] == "2025-05-01T10:45:10Z"
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
    assert bundle["decision"] in {"corroborated", "inconclusive", "not_observed"}


def test_log_evidence_defaults_service_from_tenant():
    params = {
        "tenant": "api",
        "query_type": "errors",
        "time_window": {"start": "2025-05-01T10:40:00Z", "end": "2025-05-01T11:00:00Z"},
    }
    bundle = registry.run_tool("log_evidence", params)
    validate_payload(bundle, "evidence_bundle.schema.json")
    assert bundle["metadata"]["log_entry_count"] > 0


def test_log_evidence_empty_window_has_explainer():
    params = {
        "service": "api",
        "query_type": "errors",
        "time_window": {"start": "2025-04-01T00:00:00Z", "end": "2025-04-01T01:00:00Z"},
    }
    bundle = registry.run_tool("log_evidence", params)
    assert bundle["observed_incident"] is False
    assert bundle["metadata"]["log_entry_count"] == 0
    assert "did not find entries" in bundle["summary_external"].lower()


def test_log_evidence_summary_is_sanitized():
    params = {
        "service": "api",
        "query_type": "errors",
        "time_window": {"start": "2025-05-01T10:40:00Z", "end": "2025-05-01T11:00:00Z"},
    }
    bundle = registry.run_tool("log_evidence", params)
    summary = bundle["summary_external"]
    assert "@" not in summary
    assert "http" not in summary
    assert not any(len(token) >= 32 for token in summary.split())
