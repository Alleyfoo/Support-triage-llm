from pathlib import Path

from tools import ingest_eml


def test_parse_eml_extracts_body_and_sender():
    path = Path("tests/fixtures/sample_email.eml")
    msg = ingest_eml.parse_eml(path)
    assert "contoso.com" in msg["text"].lower()
    assert "alice@example.com" in msg["end_user_handle"]
    assert msg["channel"] == "email"
