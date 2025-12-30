import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.knowledge import load_knowledge
import app.pipeline as pipeline


def test_knowledge_template_contains_founded_year():
    knowledge = load_knowledge()
    assert knowledge["founded_year"] == "1990"


def test_hints_take_priority():
    hints = ["premium_support", "support_hours"]
    detected = pipeline.detect_expected_keys("This email does not matter", hints=hints)
    assert detected == hints
