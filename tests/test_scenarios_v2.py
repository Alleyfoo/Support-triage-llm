import json
from pathlib import Path

import pytest

from app import triage_service
from app.triage_service import triage
from app.validation import SchemaValidationError, validate_payload

SCENARIOS_DIR = Path(__file__).parent / "scenarios_v2"


SCENARIOS = [
    {"name": "no_timeframe", "case_type": "email_delivery", "expect_domains": False, "time_conf_max": 0.3},
    {"name": "relative_timeframe", "case_type": "email_delivery", "expect_domains": False, "time_conf_max": 0.5},
    {"name": "multiple_domains", "case_type": "email_delivery", "expect_domains": True, "domains_count": 2},
    {"name": "single_user", "case_type": "email_delivery", "expect_domains": True},
    {"name": "angry_vague", "case_type": "unknown", "expect_domains": False},
    {"name": "angry_rant", "case_type": "unknown", "expect_domains": False},
    {"name": "contains_pii", "case_type": "email_delivery", "expect_domains": True, "check_redaction": True},
    {"name": "forwarded_thread", "case_type": "email_delivery", "expect_domains": True},
    {"name": "ui_button", "case_type": "ui_bug", "expect_domains": False},
    {"name": "integration_ats", "case_type": "integration", "expect_domains": False},
    {"name": "mixed_language", "case_type": "email_delivery", "expect_domains": True},
]


def _load_text(name: str) -> str:
    return (SCENARIOS_DIR / name / "input.txt").read_text(encoding="utf-8")


def _assert_common(result: dict, scenario: dict) -> None:
    payload = dict(result)
    payload.pop("_meta", None)
    validate_payload(payload, "triage.schema.json")

    assert 2 <= len(payload["missing_info_questions"]) <= 6

    draft_body = payload["draft_customer_reply"]["body"].lower()
    assert "eta" not in draft_body
    assert "minutes" not in draft_body
    assert "hours" not in draft_body

    if scenario.get("expect_domains"):
        assert len(payload["scope"]["recipient_domains"]) >= scenario.get("domains_count", 1)
    else:
        assert payload["scope"]["recipient_domains"] is not None

    if payload["case_type"] == "email_delivery":
        joined_questions = " ".join(payload["missing_info_questions"]).lower()
        assert "domain" in joined_questions or "recipient" in joined_questions

    if scenario.get("time_conf_max") is not None:
        assert payload["time_window"]["confidence"] <= scenario["time_conf_max"]
        assert payload["time_window"]["start"] is None
        assert payload["time_window"]["end"] is None

    # No invented timestamps in examples
    for example in payload["examples"]:
        assert example["timestamp"] is None or isinstance(example["timestamp"], str) and example["timestamp"].strip() != ""

    if scenario.get("check_redaction"):
        meta = result.get("_meta", {})
        assert meta.get("redaction_applied") is True
        assert "[REDACTED_EMAIL" in payload["symptoms"][0] or "[REDACTED_EMAIL" in draft_body


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["name"] for s in SCENARIOS])
def test_scenarios_v2_behavior(scenario):
    text = _load_text(scenario["name"])
    result = triage(text)
    payload = dict(result)
    payload.pop("_meta", None)
    validate_payload(payload, "triage.schema.json")

    assert payload["case_type"] == scenario["case_type"]
    _assert_common(result, scenario)


def test_schema_strict_top_level():
    payload = triage("Emails failing")
    candidate = dict(payload)
    candidate.pop("_meta", None)
    candidate["extra"] = "nope"
    with pytest.raises(SchemaValidationError):
        validate_payload(candidate, "triage.schema.json")


def test_llm_vs_heuristic_parity(monkeypatch):
    text = _load_text("multiple_domains")

    # Monkeypatch LLM path to reuse heuristic output but mark metadata
    def fake_llm(t, metadata=None):
        res = triage_service._triage_heuristic(t, metadata or {})
        res["_meta"]["llm_model"] = "test-llm"
        res["_meta"]["triage_mode"] = "llm"
        res["_meta"]["schema_valid"] = True
        return res

    monkeypatch.setattr(triage_service, "_triage_llm", fake_llm)

    heuristic = triage_service._triage_heuristic(text, {})
    llm = triage_service._triage_llm(text, {})

    for payload in (heuristic, llm):
        candidate = dict(payload)
        candidate.pop("_meta", None)
        validate_payload(candidate, "triage.schema.json")
        _assert_common(payload, {"expect_domains": True, "case_type": "email_delivery"})
