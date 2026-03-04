from pathlib import Path


def test_ui_contains_learning_eligible_checkbox_label():
    text = Path("ui/app.py").read_text(encoding="utf-8")
    assert "Mark as training example (learning eligible)" in text
