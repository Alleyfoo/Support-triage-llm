from app import triage_service, config


def test_heuristic_triage_returns_empty_suggested_tools(monkeypatch):
    monkeypatch.setattr(config, "TRIAGE_MODE", "heuristic")
    out = triage_service.triage("Email bounce issue for contoso.com", metadata={"case_id": "c-1"})
    assert out.get("suggested_tools") == []
