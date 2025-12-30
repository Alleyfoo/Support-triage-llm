#!/usr/bin/env python3
"""
Integration test for the Dynamic Few-Shot Learning Loop.

Flow:
1) Baseline triage of a known phrase.
2) Inject a synthetic golden example teaching a "nonsense rule".
3) Force vector store refresh.
4) Re-triage and report whether the model picked up the taught behavior.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.triage_service import triage
from app.vector_store import get_store

TEST_QUERY = "My flux capacitor is wobbling ominously."
LEARNING_DIR = Path("data/learning")
GOLDEN_PATH = LEARNING_DIR / "golden_dataset.jsonl"
BACKUP_PATH = LEARNING_DIR / "golden_dataset.jsonl.bak"


def _reset_backup() -> None:
    if BACKUP_PATH.exists():
        shutil.move(str(BACKUP_PATH), str(GOLDEN_PATH))


def _backup() -> None:
    if GOLDEN_PATH.exists():
        shutil.copy(str(GOLDEN_PATH), str(BACKUP_PATH))


def _append_example() -> None:
    example = {
        "input_symptoms": TEST_QUERY,
        "input_redacted": TEST_QUERY,
        "perfect_triage": {
            "case_type": "data_import",
            "severity": "critical",
            "draft_customer_reply": {"subject": "", "body": ""},
        },
        "perfect_reply": {"subject": "", "body": "Hold on to your timeline, we are checking the capacitor."},
        "reasoning": "Teaching synthetic rule: flux capacitor => data_import/critical",
        "edit_distance": 0.0,
    }
    with GOLDEN_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(example) + "\n")


def main() -> int:
    print("--- Learning Loop Verification ---")
    LEARNING_DIR.mkdir(parents=True, exist_ok=True)
    if not GOLDEN_PATH.exists():
        GOLDEN_PATH.touch()
    _backup()

    try:
        # Baseline
        get_store(force_refresh=True)
        print(f"\n[1/3] Baseline triage for: '{TEST_QUERY}'")
        res_base = triage(TEST_QUERY)
        base_type = res_base.get("case_type")
        base_sev = res_base.get("severity")
        print(f"      Result: {base_type} (Severity: {base_sev})")

        # Inject synthetic rule
        print("\n[2/3] Injecting synthetic golden example...")
        _append_example()

        # Refresh store and re-run
        get_store(force_refresh=True)
        print(f"\n[3/3] Post-learning triage...")
        res_learned = triage(TEST_QUERY)
        learned_type = res_learned.get("case_type")
        learned_sev = res_learned.get("severity")
        print(f"      Result: {learned_type} (Severity: {learned_sev})")

        if learned_type == "data_import" and learned_sev == "critical":
            print("\nSUCCESS: System learned the new rule dynamically!")
        else:
            print(f"\nFAILURE: System did not pick up the rule. Got {learned_type}/{learned_sev}")
    finally:
        _reset_backup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
