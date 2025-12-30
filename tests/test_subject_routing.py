from app.pipeline import run_pipeline


def test_reply_subject_escalates():
    email_text = "Thanks for the update."
    metadata = {"subject": "Re: Ticket 123", "customer_email": "alice@example.com"}

    result = run_pipeline(email_text, metadata=metadata)

    assert result["expected_keys"] == []
    assert result["answers"] == {}
    assert result["evaluation"] == {"score": 0.0, "matched": [], "missing": []}
    assert "forward to a human" in result["reply"].lower()
