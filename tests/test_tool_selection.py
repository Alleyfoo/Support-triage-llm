from app import config
from tools import triage_worker


def test_tool_select_mode_llm_falls_back_rules(monkeypatch):
    monkeypatch.setattr(config, "TOOL_SELECT_MODE", "llm")
    triage_result = {
        "case_type": "email_delivery",
        "scope": {"recipient_domains": ["example.com"]},
    }
    tools = triage_worker._select_tools(triage_result)
    assert any(t["name"] == "fetch_email_events_sample" for t in tools)


def test_tool_selection_llm_respects_suggestions(monkeypatch):
    monkeypatch.setattr(config, "TOOL_SELECT_MODE", "llm")
    triage_result = {
        "case_type": "integration",
        "scope": {"recipient_domains": []},
        "suggested_tools": [
            {"tool_name": "fetch_integration_events_sample", "params": {"integration_name": "ats"}}
        ],
    }
    tools = triage_worker._select_tools(triage_result)
    assert tools[0]["name"] == "fetch_integration_events_sample"
