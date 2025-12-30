import json
from pathlib import Path

from app.triage_service import triage
from app.validation import validate_payload

SCENARIOS_DIR = Path(__file__).parent / "scenarios"


def _load_scenarios():
    for scenario_dir in SCENARIOS_DIR.iterdir():
        if not scenario_dir.is_dir():
            continue
        input_path = scenario_dir / "input.txt"
        expected_path = scenario_dir / "expected.json"
        if input_path.exists() and expected_path.exists():
            yield scenario_dir.name, input_path.read_text(encoding="utf-8"), json.loads(expected_path.read_text(encoding="utf-8"))


def test_scenarios_triage_schema_and_required_fields():
    for name, text, expected in _load_scenarios():
        result = triage(text)
        payload = dict(result)
        payload.pop("_meta", None)
        validate_payload(payload, "triage.schema.json")
        result = payload
        assert result["case_type"] == expected["case_type"], f"{name} case_type mismatch"
        assert result["severity"] == expected["severity"], f"{name} severity mismatch"
        assert len(result["missing_info_questions"]) >= 2
        assert result["draft_customer_reply"]["body"]
        assert result["time_window"]["start"] is None
        assert result["time_window"]["end"] is None
