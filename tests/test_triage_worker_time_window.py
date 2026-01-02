from tools import triage_worker


def test_derive_query_time_window_infers_end_when_missing():
    triage_result = {"time_window": {"start": "2025-05-01T10:00:00Z", "end": None}}
    tw = triage_worker._derive_query_time_window(triage_result, {"time_window_anchor": "2025-05-01T00:00:00Z"})
    assert tw["start"].startswith("2025-05-01T10:00:00Z")
    assert tw["end"].startswith("2025-05-01T12:00:00Z")
    assert tw["reason"] == "triage_time_window_inferred_end"


def test_log_tool_runs_for_incident_with_vague_time():
    triage_result = {"case_type": "incident", "time_window": {"start": None, "end": None, "confidence": 0.2}, "symptoms": ["down yesterday afternoon"]}
    tools = triage_worker._select_tools(triage_result)
    assert any(t["name"] == "log_evidence" for t in tools)


def test_log_tool_runs_for_incident_with_no_time_and_default_window():
    triage_result = {"case_type": "incident", "time_window": {"start": None, "end": None, "confidence": 0.1}}
    query_tw = triage_worker._derive_query_time_window(triage_result, {"time_window_anchor": "2025-05-01T00:00:00Z"})
    assert query_tw["reason"] in {"fallback_last24h", "default_no_date"}
    tools = triage_worker._select_tools(triage_result)
    assert any(t["name"] == "log_evidence" for t in tools)


def test_log_tool_query_type_uses_outage_language():
    triage_result = {"case_type": "incident", "symptoms": ["service unavailable for 5 minutes"], "time_window": {"start": None, "end": None, "confidence": 0.2}}
    tools = triage_worker._select_tools(triage_result)
    log_tool = next(t for t in tools if t["name"] == "log_evidence")
    assert log_tool["params"]["query_type"] == "availability"


def test_append_log_statement_observed_and_clean():
    draft = {"subject": "Update", "body": "Initial draft"}
    log_bundle = {
        "evidence_type": "logs",
        "observed_incident": True,
        "decision": "corroborated",
        "incident_window": {"start": "2025-05-01T10:40:00Z", "end": "2025-05-01T10:58:00Z"},
        "metadata": {"query_type": "availability"},
    }
    updated = triage_worker._append_log_statement(draft, [log_bundle])
    assert "availability" in updated["body"]
    assert "10:40" in updated["body"]
    assert "10:58" in updated["body"]

    clean_bundle = {
        "evidence_type": "logs",
        "observed_incident": False,
        "decision": "not_observed",
        "incident_window": {"start": "2025-05-01T10:40:00Z", "end": "2025-05-01T10:58:00Z"},
        "metadata": {"query_type": "errors"},
    }
    updated_clean = triage_worker._append_log_statement(draft, [clean_bundle])
    assert "did not observe errors anomalies" in updated_clean["body"]
