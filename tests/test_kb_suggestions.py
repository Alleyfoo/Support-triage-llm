from pathlib import Path
import json

from tools import kb_suggestions


def test_kb_suggestions_writes_file(tmp_path, monkeypatch):
    # Fake fetch_queue to return a row with a report containing kb_suggestions
    sample_rows = [
        {
            "case_id": "case-1",
            "final_report_json": json.dumps(
                {
                    "classification": {"failure_stage": "recipient", "confidence": 0.5, "top_reasons": []},
                    "kb_suggestions": ["Email delivery troubleshooting"],
                    "engineering_escalation": {"evidence_refs": ["evt-1"]},
                }
            ),
        }
    ]

    monkeypatch.setattr(kb_suggestions.queue_db, "fetch_queue", lambda limit=500: sample_rows)
    out = tmp_path / "kb.jsonl"
    suggestions = kb_suggestions.collect()
    kb_suggestions.write_suggestions(out, suggestions)
    assert out.exists()
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["case_id"] == "case-1"
    assert "evidence_refs" in payload
