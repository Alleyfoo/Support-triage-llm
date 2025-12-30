import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.knowledge import load_knowledge
from app.pipeline import run_pipeline


def test_reply_includes_founded_and_headquarters():
    email = "Hello, when were you founded and where are you based?"
    result = run_pipeline(email)
    reply = result["reply"].lower()
    assert "1990" in reply
    assert "helsinki" in reply
    assert set(["founded_year", "headquarters"]).issubset(result["evaluation"]["matched"])
    assert result["evaluation"]["score"] == 1.0


def test_pipeline_runs_without_metadata():
    email = "Hello there, when were you founded?"
    result = run_pipeline(email)
    assert isinstance(result["reply"], str)
    assert "founded_year" in result["expected_keys"]
    assert result["evaluation"]["score"] >= 0


def test_support_hours_question():
    email = "Could you tell me your support hours?"
    result = run_pipeline(email)
    assert "support_hours" in result["expected_keys"]
    assert "09:00" in result["reply"]
    assert result["evaluation"]["score"] == 1.0


def test_key_code_lookup_returns_canonical_warranty():
    email = "My repair ticket references AG-445. Can you confirm what it covers?"
    result = run_pipeline(email)
    knowledge = load_knowledge()
    key = "key_code_AG-445"
    assert key in result["expected_keys"]
    canonical_text = knowledge[key]
    assert canonical_text in result["reply"]
    assert result["answers"].get(key) == canonical_text
    assert result["evaluation"]["score"] == 1.0
    assert key in result["evaluation"]["matched"]
