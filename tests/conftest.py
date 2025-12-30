import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import pipeline


@pytest.fixture(autouse=True)
def _isolate_pipeline_history(tmp_path, monkeypatch):
    """Ensure tests do not write to the real pipeline history file."""

    history_path = tmp_path / "pipeline_history.xlsx"
    monkeypatch.setattr(pipeline, "PIPELINE_LOG_PATH", str(history_path))
