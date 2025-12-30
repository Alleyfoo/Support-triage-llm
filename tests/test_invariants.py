import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import app.pipeline as pipeline


def test_partial_score_when_value_missing(monkeypatch):
    def fake_generate(email_text: str, knowledge, expected_keys, **kwargs):
        return {"reply": "We were founded in 1990.", "answers": {"founded_year": knowledge["founded_year"]}}

    monkeypatch.setattr(pipeline, "generate_email_reply", fake_generate)
    result = pipeline.run_pipeline("Where are you based and when were you founded?")
    assert result["evaluation"]["score"] == pytest.approx(0.5)
    assert "headquarters" in result["evaluation"]["missing"]
    assert "founded_year" in result["evaluation"]["matched"]
