from tools import triage_worker


def test_tool_select_falls_back_to_rules_for_email_delivery():
    triage_result = {
        "case_type": "email_delivery",
        "scope": {"recipient_domains": ["example.com"]},
    }
    tools = triage_worker._select_tools(triage_result)
    names = {t["name"] for t in tools}
    assert "fetch_email_events_sample" in names
    assert "dns_email_auth_check_sample" in names


def test_tool_selection_ignores_llm_suggested_tools():
    triage_result = {
        "case_type": "integration",
        "scope": {"recipient_domains": []},
        "suggested_tools": [
            {"tool_name": "fetch_email_events_sample", "params": {"recipient_domain": "attacker.com"}}
        ],
    }
    tools = triage_worker._select_tools(triage_result)
    names = [t["name"] for t in tools]
    assert names == ["fetch_integration_events_sample"]
