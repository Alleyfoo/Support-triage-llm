import json
from pathlib import Path

from tools.process_queue import init_queue, process_once, load_queue


def test_queue_worker_processing(tmp_path, monkeypatch):
    dataset = [
        {
            "id": 1,
            "customer": "Alice",
            "subject": "Company background",
            "body": "When were you founded?",
            "expected_keys": ["founded_year"],
        }
    ]
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps(dataset), encoding="utf-8")

    queue_path = tmp_path / "queue.xlsx"
    init_queue(queue_path, dataset_path, overwrite=True)

    processed = process_once(queue_path, agent_name="agent-test")
    assert processed is True

    df = load_queue(queue_path)
    row = df.iloc[0]
    assert str(row["status"]).lower() == "done"
    assert "Aurora" in str(row["reply"])  # stub mentions Aurora Gadgets
    assert float(row["score"]) >= 0.0
    assert str(row["raw_body"]) == "When were you founded?"


def test_queue_marks_human_review(tmp_path):
    dataset = [
        {
            "id": 2,
            "customer": "Bob",
            "subject": "Lunch",
            "body": "Want to grab lunch tomorrow?",
            "expected_keys": [],
        }
    ]
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps(dataset), encoding="utf-8")
    queue_path = tmp_path / "queue.xlsx"
    init_queue(queue_path, dataset_path, overwrite=True)

    processed = process_once(queue_path, agent_name="agent-test")
    assert processed is True
    df = load_queue(queue_path)
    row = df.iloc[0]
    assert str(row["status"]).lower() == "human-review"
    assert row["reply"] == ""
    assert str(row["raw_body"]) == "Want to grab lunch tomorrow?"
