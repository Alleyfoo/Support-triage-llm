
#!/usr/bin/env python3
"""Send N prompts to the pipeline and log raw replies."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import List

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))
from app.pipeline import run_pipeline
from app.config import MODEL_BACKEND, OLLAMA_MODEL, OLLAMA_HOST


def expand_prompts(prompt: str, count: int) -> List[str]:
    return [prompt for _ in range(count)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark repeated prompts against the pipeline")
    parser.add_argument("--prompt", default="Send me back a number.", help="Prompt text to send")
    parser.add_argument("--count", type=int, default=100, help="Number of iterations to run")
    parser.add_argument("--output", default="data/prompt_benchmark.xlsx", help="Excel output file")
    parser.add_argument("--log-csv", default="data/prompt_benchmark_log.csv", help="CSV log file with results")
    parser.add_argument("--warmup", type=int, default=0, help="Warmup iterations before measurement")
    parser.add_argument("--include-prompts", action="store_true", help="Include prompt context in the log")
    parser.add_argument(
        "--expected-key",
        action="append",
        help=(
            "Add an expected knowledge key to metadata (repeatable). "
            "Prevents human_review fallback so a real model call is made."
        ),
    )
    args = parser.parse_args()

    # Brief banner to make it obvious which backend is in use
    if MODEL_BACKEND == "ollama":
        print(f"Backend: ollama model={OLLAMA_MODEL or '(unset)'} host={OLLAMA_HOST}")
    else:
        print(f"Backend: {MODEL_BACKEND}")

    prompts = expand_prompts(args.prompt, args.count)

    if args.warmup > 0:
        for _ in range(args.warmup):
            run_pipeline(args.prompt, metadata={"expected_keys": args.expected_key} if args.expected_key else None)

    records = []
    for idx, prompt in enumerate(prompts, start=1):
        started = time.perf_counter()
        result = run_pipeline(prompt, metadata={"expected_keys": args.expected_key} if args.expected_key else None)
        elapsed = time.perf_counter() - started
        record = {
            "iteration": idx,
            "prompt": prompt,
            "reply": result.get("reply", ""),
            "elapsed_seconds": round(elapsed, 4),
            "score": result.get("evaluation", {}).get("score"),
            "human_review": bool(result.get("human_review")),
        }
        if args.expected_key:
            record["expected_keys"] = ", ".join(args.expected_key)
        if args.include_prompts:
            record["answers"] = json.dumps(result.get("answers", {}), ensure_ascii=False)
        records.append(record)

    df = pd.DataFrame.from_records(records)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="results")

    log_path = Path(args.log_csv)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(log_path, index=False)

    print(f"Processed {len(df)} prompts")
    print(f"Average latency: {df['elapsed_seconds'].mean():.3f} seconds")
    print(f"Results written to: {out_path}")
    print(f"CSV log written to: {log_path}")


if __name__ == "__main__":
    main()
