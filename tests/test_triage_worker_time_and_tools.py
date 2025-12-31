from datetime import datetime, timezone

from tools import triage_worker as tw


def test_derive_query_time_window_prefers_parsed_and_anchor():
    triage_result = {"time_window": {"start": "2025-12-30T18:00:00Z", "end": None, "confidence": 0.6}}
    meta = {
        "time_window_anchor": "2025-12-31T12:00:00Z",
        "time_window_reason": "parsed_from_text",
        "time_window_source": "heuristic",
    }
    tw_out = tw._derive_query_time_window(triage_result, meta)
    assert tw_out["start"] == "2025-12-30T18:00:00Z"
    assert tw_out["end"].startswith("2025-12-30")  # inferred end
    assert tw_out["reason"] == "parsed_from_text"
    assert tw_out["anchor"] == "2025-12-31T12:00:00Z"


def test_derive_query_time_window_fallback_last24h():
    triage_result = {"time_window": {"start": None, "end": None, "confidence": 0.1}}
    anchor = datetime(2025, 12, 31, 12, 0, tzinfo=timezone.utc)
    meta = {"time_window_anchor": anchor.isoformat().replace("+00:00", "Z"), "time_window_source": "heuristic"}
    tw_out = tw._derive_query_time_window(triage_result, meta)
    assert tw_out["reason"] == "fallback_last24h"
    assert tw_out["start"].startswith("2025-12-30")
    assert tw_out["end"].startswith("2025-12-31")


def test_select_tools_gates_by_case_type():
    triage_result = {
        "case_type": "incident",
        "symptoms": ["api down"],
        "draft_customer_reply": {"subject": "", "body": "api down"},
        "suggested_tools": [{"tool_name": "fetch_email_events_sample", "params": {}}],
    }
    tools = tw._select_tools(triage_result)
    names = {t["name"] for t in tools}
    assert "log_evidence" in names
    assert "fetch_email_events_sample" not in names


def test_partition_evidence_filters_by_allowed():
    bundles = [
        {"metadata": {"tool_name": "log_evidence"}, "source": "logs"},
        {"metadata": {"tool_name": "fetch_email_events_sample"}, "source": "email_events"},
    ]
    parts = tw._partition_evidence(bundles, {"log_evidence"})
    assert len(parts["relevant"]) == 1
    assert len(parts["other"]) == 1
