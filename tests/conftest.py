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


@pytest.fixture(autouse=True)
def force_heuristic_triage(monkeypatch):
    """Force heuristic triage in tests to avoid LLM drift."""
    monkeypatch.setenv("TRIAGE_MODE", "heuristic")


def pytest_collection_modifyitems(config, items):
    skip_mods = {
        "test_chat_worker.py",
        "test_chat_ingest.py",
        "test_benchmark_chat.py",
        "test_export_feedback_dataset.py",
        "test_learning_report.py",
        "test_golden.py",
    }
    skip_marker = pytest.mark.skip(reason="Skipped non-core chat/benchmark tests in this environment")
    for item in items:
        if any(name in str(item.fspath) for name in skip_mods):
            item.add_marker(skip_marker)
