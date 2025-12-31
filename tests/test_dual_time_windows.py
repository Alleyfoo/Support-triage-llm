from datetime import datetime, timezone

from tools import triage_worker
from app import triage_service, report_service


def test_customer_time_window_parsed_and_metadata_kept():
    text = "Since 18:00 UTC yesterday our API is failing"
    anchor = datetime(2025, 12, 31, 12, 0, tzinfo=timezone.utc)
    metadata = {"received_at": anchor.isoformat().replace("+00:00", "Z")}
    triage_result = triage_service._base_triage_payload(text, metadata)
    triage_result["_meta"] = {"time_window_anchor": metadata["received_at"], "time_window_reason": "parsed_from_text"}
    query_tw = triage_worker._derive_query_time_window(triage_result, triage_result["_meta"])
    customer_tw = triage_worker._customer_time_window(triage_result, triage_result["_meta"])
    assert customer_tw["reason"] in {"parsed_from_text", "parsed_none"}
    assert customer_tw["start"].startswith("2025-12-30T18:00:00Z")
    assert query_tw["reason"] in {"fallback_last24h", "parsed_from_text", "triage_time_window"}


def test_evidence_metadata_includes_both_windows():
    triage_result = {
        "case_type": "incident",
        "time_window": {"start": "2025-12-30T18:00:00Z", "end": None, "confidence": 0.6},
        "time_window_reason": "parsed_from_text",
    }
    meta = {"time_window_anchor": "2025-12-31T10:00:00Z", "time_window_reason": "parsed_from_text"}
    customer_tw = triage_worker._customer_time_window(triage_result, meta)
    assert customer_tw["start"] == "2025-12-30T18:00:00Z"
    assert customer_tw["reason"] == "parsed_from_text"
    query_tw = triage_worker._derive_query_time_window(triage_result, meta)
    assert "start" in query_tw and "end" in query_tw


def test_draft_includes_customer_window_sentence():
    draft = {"subject": "Update", "body": "Initial draft"}
    customer_tw = {"start": "2025-12-30T18:00:00Z", "end": None, "reason": "parsed_from_text"}
    log_bundle = {
        "evidence_type": "logs",
        "observed_incident": False,
        "incident_window": {"start": "2025-12-30T18:05:00Z", "end": "2025-12-31T10:00:00Z"},
        "metadata": {"query_type": "errors"},
    }
    updated = triage_worker._append_log_statement(draft, [log_bundle], "unknown", customer_tw)
    assert "Customer reports issues since 2025-12-30T18:00:00Z" in updated["body"]


def test_report_includes_customer_window_sentence():
    triage_json = {
        "case_type": "incident",
        "customer_time_window": {"start": "2025-12-30T18:00:00Z", "end": None, "reason": "parsed_from_text"},
    }
    bundles = []
    report = report_service.generate_report(triage_json, bundles)
    body = report["customer_update"]["body"]
    assert "Customer reports issues since 2025-12-30T18:00:00Z" in body
