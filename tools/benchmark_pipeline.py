
#!/usr/bin/env python3
"""Benchmark the pipeline across a dataset and optionally log per-email timings."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.pipeline import run_pipeline, load_knowledge
from app.slm_llamacpp import build_prompt


def _load_emails(path: Path) -> List[Dict[str, object]]:
    if not path.exists():
        raise SystemExit(f"Email dataset not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit("Dataset must be a list of email objects")
    return data


def _expand_dataset(emails: List[Dict[str, object]], count: Optional[int]) -> List[Dict[str, object]]:
    if not count or count <= len(emails):
        return emails if not count else emails[:count]
    expanded: List[Dict[str, object]] = []
    cycles = math.floor(count / len(emails))
    remainder = count % len(emails)
    idx = 0
    for cycle in range(cycles):
        for email in emails:
            idx += 1
            clone = dict(email)
            clone["_bench_id"] = idx
            clone["run_cycle"] = cycle
            expanded.append(clone)
    for email in emails[:remainder]:
        idx += 1
        clone = dict(email)
        clone["_bench_id"] = idx
        clone["run_cycle"] = cycles
        expanded.append(clone)
    return expanded


def benchmark(emails: List[Dict[str, object]], *, include_prompts: bool = False) -> pd.DataFrame:
    knowledge = load_knowledge()
    records: List[Dict[str, object]] = []
    for i, email in enumerate(emails, start=1):
        body = str(email.get("body", ""))
        metadata = {"expected_keys": email.get("expected_keys", [])} if email.get("expected_keys") else {}
        started = time.perf_counter()
        result = run_pipeline(body, metadata=metadata if metadata else None)
        elapsed = time.perf_counter() - started
        evaluation = result.get("evaluation", {}) or {}
        record: Dict[str, object] = {
            "bench_index": email.get("_bench_id", i),
            "id": email.get("id"),
            "subject": email.get("subject"),
            "customer": email.get("customer"),
            "elapsed_seconds": round(elapsed, 4),
            "score": evaluation.get("score"),
            "matched": ", ".join(evaluation.get("matched", [])),
            "missing": ", ".join(evaluation.get("missing", [])),
            "reply": result.get("reply", ""),
            "answers": json.dumps(result.get("answers", {}), ensure_ascii=False),
            "expected_keys": ", ".join(result.get("expected_keys", [])),
            "human_review": bool(result.get("human_review")),
        }
        if include_prompts and not result.get("human_review"):
            prompt = build_prompt(body, knowledge, result.get("expected_keys", []))
            record["prompt"] = prompt
        records.append(record)
    return pd.DataFrame.from_records(records)


def _safe_mean(series: pd.Series) -> float:
    numeric = pd.to_numeric(series, errors="coerce")
    return float(numeric.mean()) if not numeric.empty else 0.0


def _safe_min(series: pd.Series) -> float:
    numeric = pd.to_numeric(series, errors="coerce")
    return float(numeric.min()) if not numeric.empty else 0.0


def write_report(emails: List[Dict[str, object]], results: pd.DataFrame, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    summary = pd.DataFrame(
        [
            {
                "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "emails_processed": int(results.shape[0]),
                "avg_latency_seconds": round(float(results["elapsed_seconds"].mean()), 4),
                "p95_latency_seconds": round(float(results["elapsed_seconds"].quantile(0.95)), 4),
                "avg_score": round(_safe_mean(results["score"]), 4),
                "min_score": round(_safe_min(results["score"]), 4),
                "human_review_count": int(results["human_review"].sum()),
            }
        ]
    )
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        pd.DataFrame(emails).to_excel(writer, index=False, sheet_name="emails")
        results.to_excel(writer, index=False, sheet_name="results")
        summary.to_excel(writer, index=False, sheet_name="summary")


def maybe_write_log(results: pd.DataFrame, log_csv: Optional[Path]) -> None:
    if not log_csv:
        return
    log_csv.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(log_csv, index=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark pipeline across sample emails")
    parser.add_argument("--dataset", default="data/test_emails.json", help="Path to JSON dataset")
    parser.add_argument("--count", type=int, help="Process at least this many emails (dataset is duplicated as needed)")
    parser.add_argument("--output", default="data/benchmark_report.xlsx", help="Excel workbook output path")
    parser.add_argument("--log-csv", help="Optional CSV file capturing per-email timings")
    parser.add_argument("--include-prompts", action="store_true", help="Include LLM prompt text in the log/results")
    parser.add_argument("--warmup", type=int, default=0, help="Number of warmup runs before measurement")
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    base_emails = _load_emails(dataset_path)
    emails = _expand_dataset(base_emails, args.count)

    if args.warmup > 0:
        _ = benchmark(base_emails, include_prompts=False)

    results = benchmark(emails, include_prompts=args.include_prompts)
    out_path = Path(args.output)
    write_report(emails, results, out_path)

    log_csv = Path(args.log_csv) if args.log_csv else None
    maybe_write_log(results, log_csv)

    print(f"Processed {len(emails)} emails")
    print(f"Average latency: {results['elapsed_seconds'].mean():.3f} seconds")
    if log_csv:
        print(f"Per-email log written to: {log_csv}")
    print(f"Results written to: {out_path}")


if __name__ == "__main__":
    main()
