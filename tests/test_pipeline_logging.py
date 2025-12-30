import json
import os
import sys

import pandas as pd

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import pipeline


def test_key_code_lookup_and_reply_contains_canonical_value(monkeypatch, tmp_path):
    log_path = tmp_path / "history.xlsx"
    monkeypatch.setattr(pipeline, "PIPELINE_LOG_PATH", str(log_path))

    email = "Hello team, key AG-445 came up in my ticket."
    result = pipeline.run_pipeline(email)

    assert result["expected_keys"] == ["key_code_AG-445"]
    assert result["evaluation"]["score"] == 1.0
    assert "two full years" in result["reply"].lower()
    assert (
        result["answers"].get("key_code_AG-445")
        == "Our warranty policy covers every Aurora device for two full years."
    )


def test_pipeline_appends_rows_to_excel_history(monkeypatch, tmp_path):
    log_path = tmp_path / "history.xlsx"
    monkeypatch.setattr(pipeline, "PIPELINE_LOG_PATH", str(log_path))

    email_one = "When were you founded?"
    email_two = "Where are you based?"

    result_one = pipeline.run_pipeline(email_one)
    result_two = pipeline.run_pipeline(email_two)

    assert log_path.exists()

    frame = pd.read_excel(log_path)
    assert list(frame["email"]) == [email_one, email_two]

    first_expected = json.loads(frame.loc[0, "expected_keys"])
    second_expected = json.loads(frame.loc[1, "expected_keys"])

    assert first_expected == result_one["expected_keys"]
    assert second_expected == result_two["expected_keys"]
    assert frame.loc[0, "reply"] == result_one["reply"]
    assert frame.loc[1, "score"] == result_two["evaluation"]["score"]
