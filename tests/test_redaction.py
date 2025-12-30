from app.redaction import redact


def test_redacts_email_and_phone():
    text = "Contact me at user@example.com or +1 415-555-1234."
    result = redact(text)
    assert "[REDACTED_EMAIL@example.com]" in result["redacted_text"]
    assert "[REDACTED_PHONE]" in result["redacted_text"]
    assert result["redaction_applied"] is True


def test_leaves_technical_ids():
    text = "Failure id=evt-1234 at 2025-12-29T09:10:00Z"
    result = redact(text)
    assert "evt-1234" in result["redacted_text"]
    assert "2025-12-29T09:10:00Z" in result["redacted_text"]
    assert result["redaction_applied"] is False


def test_keeps_domains_without_emails():
    text = "Issue affecting contoso.com only."
    result = redact(text)
    assert "contoso.com" in result["redacted_text"]
    assert result["redaction_applied"] is False


def test_redacts_email_but_keeps_domain_hint():
    text = "message-id: <abc123@example.com>"
    result = redact(text)
    assert "[REDACTED_EMAIL@example.com]" in result["redacted_text"]
