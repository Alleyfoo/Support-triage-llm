from datetime import datetime, timezone
from app.time_window import parse_time_window
from tools import registry, triage_worker


def test_incident_email_date_drives_log_pull():
    text = "Our API was down May 1 at 10:45 UTC for several customers"
    tw = parse_time_window(text, now=datetime(2025, 5, 1, tzinfo=timezone.utc))
    triage_result = {
        "case_type": "incident",
        "time_window": tw,
        "suggested_tools": [],
    }
    query_tw = triage_worker._derive_query_time_window(triage_result, {"time_window_anchor": "2025-05-01T00:00:00Z"})
    tools = triage_worker._select_tools(triage_result)
    log_tool = next(t for t in tools if t["name"] == "log_evidence")

    params = dict(log_tool["params"])
    params["time_window"] = {"start": query_tw["start"], "end": query_tw["end"]}
    params.setdefault("service", "api")
    bundle = registry.run_tool("log_evidence", params)

    assert bundle["observed_incident"] is True
    assert bundle["incident_window"]["start"].startswith("2025-05-01T10:45")
    assert bundle["incident_window"]["end"].startswith("2025-05-01T10:58")
