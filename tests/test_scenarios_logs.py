from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.triage_service import triage
from app.validation import validate_payload
from tools import registry, triage_worker

SCENARIOS_DIR = Path(__file__).parent / "scenarios_logs"


def _load_scenarios():
    for scenario_dir in SCENARIOS_DIR.iterdir():
        if not scenario_dir.is_dir():
            continue
        input_path = scenario_dir / "input.txt"
        expected_path = scenario_dir / "expected.json"
        if input_path.exists() and expected_path.exists():
            yield scenario_dir.name, input_path.read_text(encoding="utf-8"), json.loads(expected_path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name,text,expected", list(_load_scenarios()))
def test_log_scenarios(name: str, text: str, expected: dict):
    triage_result = triage(text)
    assert triage_result["case_type"] == expected["case_type"]

    meta = {}
    if expected.get("anchor"):
        meta["time_window_anchor"] = expected["anchor"]
    if expected.get("time_window"):
        query_tw = expected["time_window"]
    else:
        query_tw = triage_worker._derive_query_time_window(triage_result, meta)
    if expected.get("incident_window"):
        customer_tw = expected["incident_window"]
    else:
        customer_tw = triage_worker._customer_time_window(triage_result, meta)

    params = {
        "service": expected.get("service") or "api",
        "query_type": "errors",
        "time_window": {"start": query_tw["start"], "end": query_tw["end"]},
        "incident_window": {"start": customer_tw.get("start"), "end": customer_tw.get("end")},
        "reason": "scenario_log_gate",
    }
    bundle = registry.run_tool("log_evidence", params)
    validate_payload(bundle, "evidence_bundle.schema.json")

    assert bundle["decision"] == expected["decision"], f"{name} decision mismatch"
    assert bundle["observed_incident"] is expected["observed_incident"], f"{name} incident flag mismatch"
    if expected.get("incident_start_prefix"):
        assert bundle["incident_window"]["start"].startswith(expected["incident_start_prefix"])
    if expected.get("incident_end_prefix"):
        assert bundle["incident_window"]["end"].startswith(expected["incident_end_prefix"])
    if expected.get("log_entry_min") is not None:
        assert bundle["metadata"]["log_entry_count"] >= expected["log_entry_min"]
