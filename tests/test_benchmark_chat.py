from __future__ import annotations

from pathlib import Path

from tools import benchmark_chat


def test_run_benchmark_processes_messages(tmp_path):
    queue_path = tmp_path / "bench.xlsx"
    metrics = benchmark_chat.run_benchmark(
        queue_path,
        messages=[{"conversation_id": "c1", "text": "When were you founded?"}],
        repeat=2,
        dispatch=False,
    )
    assert metrics["processed"] == 2
    assert metrics["inserted"] == 2
    assert metrics["messages_per_second"] >= 0


def test_run_benchmark_raises_without_messages(tmp_path):
    queue_path = tmp_path / "bench.xlsx"
    try:
        benchmark_chat.run_benchmark(queue_path, messages=[], repeat=1)
    except ValueError as exc:
        assert "No messages" in str(exc)
    else:
        raise AssertionError("Expected ValueError when no messages supplied")
