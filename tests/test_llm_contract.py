import json
import os

import pytest

from app import triage_service


@pytest.fixture(autouse=True)
def force_llm(monkeypatch):
    monkeypatch.setenv("TRIAGE_MODE", "llm")


def test_llm_triage_backfills_required_fields(monkeypatch):
    """LLM path should still return schema-required fields even if the model omits them."""

    # Return a minimal JSON missing case_type/severity to exercise the fixer.
    def fake_call(prompt: str) -> str:
        return json.dumps({"draft_customer_reply": {"subject": "", "body": ""}})

    monkeypatch.setattr(triage_service, "_call_ollama", fake_call)

    res = triage_service.triage("Emails failing", metadata={})
    assert res["case_type"] in {"email_delivery", "incident", "unknown"}
    assert res["severity"] in {"critical", "high", "medium", "low"}
    assert "draft_customer_reply" in res
    assert isinstance(res["_meta"], dict)
