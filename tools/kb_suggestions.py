#!/usr/bin/env python3
"""Generate KB suggestion drafts from triage/report data (placeholder)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Dict

from app import queue_db


def collect(limit: int = 500) -> List[Dict[str, str]]:
    rows = queue_db.fetch_queue(limit=limit)
    suggestions: List[Dict[str, str]] = []
    for row in rows:
        report = row.get("final_report_json")
        if isinstance(report, str):
            try:
                report = json.loads(report)
            except json.JSONDecodeError:
                report = None
        if not isinstance(report, dict):
            continue
        case_id = row.get("case_id") or row.get("id")
        classification = report.get("classification", {})
        kb_list = report.get("kb_suggestions", []) or []
        for kb in kb_list:
            suggestions.append(
                {
                    "case_id": case_id,
                    "title": kb,
                    "reference": f"classification:{classification.get('failure_stage','unknown')}",
                    "evidence_refs": ", ".join(report.get("engineering_escalation", {}).get("evidence_refs", [])),
                }
            )
    return suggestions


def write_suggestions(path: Path, suggestions: List[Dict[str, str]]) -> None:
    payload = "\n".join(json.dumps(s, ensure_ascii=False) for s in suggestions)
    path.write_text(payload, encoding="utf-8")


def main() -> None:
    out = Path("data/kb_suggestions.jsonl")
    suggestions = collect()
    write_suggestions(out, suggestions)
    print(f"Wrote {len(suggestions)} KB suggestions to {out}")


if __name__ == "__main__":
    main()
