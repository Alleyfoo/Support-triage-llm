import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.pipeline import detect_expected_keys, run_pipeline


def test_metadata_overrides_detection():
    metadata = {"expected_keys": ["support_email"]}
    result = run_pipeline("Just saying hello", metadata=metadata)
    assert result["expected_keys"] == ["support_email"]
    assert "support@auroragadgets.example" in result["reply"]
    assert result["evaluation"]["matched"] == ["support_email"]


def test_keyword_detection_multiple_matches():
    email = "Hi, what is your warranty and how fast do you ship?"
    keys = detect_expected_keys(email)
    assert keys == ["warranty_policy", "shipping_time"]
