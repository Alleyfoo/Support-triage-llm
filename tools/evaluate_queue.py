#!/usr/bin/env python3
"""Evaluate queue replies and flag low-quality answers for CS review.

Reads `data/email_queue.xlsx`, finds rows with status == 'done' and missing
`quality_score`, runs a semantic evaluation comparing `body` (question) and
`reply`, and writes `quality_score`, `quality_issues`, and `quality_notes`.
If score < --threshold, sets status = 'human-review'.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import List

import pandas as pd

import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.evaluator import evaluate_qa
from tools.process_queue import load_queue, save_queue


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate queue replies with LLM/stub and flag low-quality answers")
    ap.add_argument("--queue", default="data/email_queue.xlsx", help="Queue workbook path")
    ap.add_argument("--threshold", type=float, default=0.7, help="Scores below this are flagged for human review")
    ap.add_argument("--limit", type=int, help="Max rows to evaluate this run")
    ap.add_argument("--agent-name", default="qa-agent", help="Identifier for this evaluator")
    args = ap.parse_args()

    path = Path(args.queue)
    df = load_queue(path)
    if df.empty:
        print("Queue empty or unreadable; nothing to evaluate.")
        return

    # Ensure columns exist
    for col in ("quality_score", "quality_issues", "quality_notes", "qa_agent", "qa_finished_at"):
        if col not in df.columns:
            df[col] = "" if col != "quality_score" else pd.NA

    mask_done = df["status"].astype(str).str.lower().eq("done")
    mask_missing = df["quality_score"].isna() | (df["quality_score"].astype(str).str.len() == 0)
    candidates = df[mask_done & mask_missing]
    if args.limit:
        candidates = candidates.head(max(args.limit, 0))

    if candidates.empty:
        print("No completed rows without quality score.")
        return

    updated_indices: List[int] = []
    for idx, row in candidates.iterrows():
        question = str(row.get("body", ""))
        answer = str(row.get("reply", ""))
        language = str(row.get("language", "")).strip() or None
        res = evaluate_qa(question, answer, language=language)
        score = float(res.get("score", 0.0))
        issues = json.dumps(res.get("issues", []), ensure_ascii=False)
        notes = res.get("explanation", "")

        df.at[idx, "quality_score"] = round(score, 3)
        df.at[idx, "quality_issues"] = issues
        df.at[idx, "quality_notes"] = notes
        df.at[idx, "qa_agent"] = args.agent_name
        df.at[idx, "qa_finished_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"

        if score < args.threshold:
            df.at[idx, "status"] = "human-review"
        updated_indices.append(idx)

    if not updated_indices:
        print("No rows evaluated.")
        return

    save_queue(path, df)
    print(f"Evaluated {len(updated_indices)} row(s). Threshold={args.threshold}")


if __name__ == "__main__":
    main()

