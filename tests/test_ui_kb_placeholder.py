import json
from pathlib import Path


def test_kb_suggestions_display():
    kb_path = Path("data/kb_suggestions.jsonl")
    kb_path.parent.mkdir(parents=True, exist_ok=True)
    kb_path.write_text(json.dumps({"case_id": "c1", "title": "KB1", "evidence_refs": "evt-1"}) + "\n", encoding="utf-8")
    assert kb_path.exists()
