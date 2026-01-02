import os

import pytest

from app.features import pipeline_enabled

if not pipeline_enabled():
    pytest.skip("Pipeline feature disabled (set FEATURE_PIPELINE=1 to enable)", allow_module_level=True)

from app.extensions.pipeline import run_pipeline


def test_pipeline_marks_human_review_when_no_expected_keys(monkeypatch):
    result = run_pipeline("Want to grab lunch tomorrow?")
    assert result.get("human_review") is True
    assert result["expected_keys"] == []
    assert result["answers"] == {}
