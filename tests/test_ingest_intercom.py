from pathlib import Path

from tools import ingest_intercom_export


def test_parse_intercom_export_sample():
    path = Path("tests/fixtures/intercom_export_sample.json")
    messages = ingest_intercom_export.parse_export(path)
    assert len(messages) == 1
    msg = messages[0]
    assert msg["conversation_id"] == "conv-001"
    assert "contoso.com" in msg["text"]
    assert msg["channel"] == "intercom"
