from pathlib import Path

from tools import registry
from app.validation import validate_payload


def test_provider_events_file_tool():
    path = Path("tests/fixtures/provider_events.json")
    bundle = registry.run_tool("fetch_email_provider_events_file", {"file_path": str(path), "tenant": "acme"})
    validate_payload(bundle, "evidence_bundle.schema.json")
    assert any(evt["type"] == "bounce" for evt in bundle["events"])


def test_app_log_events_file_tool():
    path = Path("tests/fixtures/app_events.log")
    bundle = registry.run_tool("fetch_app_log_events_file", {"file_path": str(path), "tenant": "acme"})
    validate_payload(bundle, "evidence_bundle.schema.json")
    assert any(evt["type"] == "workflow_disabled" for evt in bundle["events"])
