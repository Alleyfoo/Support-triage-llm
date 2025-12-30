import argparse
import os
import re
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List

from app.io_utils import read_table, write_table, serialize


def parse_expected_keys(raw) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw)
    parts = re.split(r"[;|,]", text)
    return [p.strip() for p in parts if p.strip()]


def main() -> None:
    ap = argparse.ArgumentParser(description="Batch process customer service emails")
    ap.add_argument("input", help="Input CSV/Excel with columns: email or text, optional expected_keys")
    ap.add_argument("-o", "--output", help="Output path (.csv or .xlsx). Default: <input>.replies.csv", default=None)
    ap.add_argument("--model-path", default=None, help="Path to .gguf model (overrides $MODEL_PATH)")
    ap.add_argument("--workers", type=int, default=4, help="Number of worker threads (default 4)")
    args = ap.parse_args()

    if args.model_path:
        os.environ["MODEL_PATH"] = str(args.model_path)
    elif "MODEL_PATH" not in os.environ:
        print("MODEL_PATH not set - falling back to deterministic stub replies.")

    t0 = time.time()
    from app.pipeline import run_pipeline

    inp = Path(args.input)
    out = Path(args.output) if args.output else inp.with_suffix(".replies.csv")

    df = read_table(str(inp))
    email_column = None
    for candidate in ("email", "text"):
        if candidate in df.columns:
            email_column = candidate
            break
    if email_column is None:
        raise SystemExit("Input must contain an 'email' or 'text' column")

    has_expected = "expected_keys" in df.columns
    customer_email_column = None
    subject_column = None
    for candidate in ("subject", "Subject"):
        if candidate in df.columns:
            subject_column = candidate
            break
    for candidate in ("customer_email", "sender_email", "from_email"):
        if candidate in df.columns:
            customer_email_column = candidate
            break

    replies: List[str] = []
    expected_col: List[str] = []
    answers_col: List[str] = []
    score_col: List[float] = []
    matched_col: List[str] = []
    missing_col: List[str] = []

    rows = df.to_dict("records")

    def process_row(row: dict):
        email_text = str(row[email_column])
        metadata: Dict[str, Any] = {}
        if customer_email_column:
            raw_customer = row.get(customer_email_column)
            if raw_customer not in (None, ""):
                customer_value = str(raw_customer).strip()
                if customer_value and customer_value.lower() != "nan":
                    metadata["customer_email"] = customer_value
        if subject_column:
            raw_subject = row.get(subject_column)
            if raw_subject not in (None, ""):
                subject_value = str(raw_subject)
                if subject_value.strip():
                    metadata["subject"] = subject_value
        if has_expected:
            expected = parse_expected_keys(row.get("expected_keys"))
            if expected:
                metadata["expected_keys"] = expected
        return run_pipeline(email_text, metadata=metadata or None)

    chunk_size = max(1, args.workers * 4)
    results: List[float] = []

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for i in range(0, len(rows), chunk_size):
            chunk = rows[i : i + chunk_size]
            for res in ex.map(process_row, chunk):
                replies.append(res["reply"])
                expected_col.append("|".join(res["expected_keys"]))
                answers_col.append(serialize(res["answers"]))
                score = float(res["evaluation"]["score"])
                score_col.append(score)
                results.append(score)
                matched_col.append(serialize(res["evaluation"]["matched"]))
                missing_col.append(serialize(res["evaluation"]["missing"]))
            if (i + len(chunk)) % 200 == 0 and len(rows) > 0:
                print(f"Processed {i + len(chunk)}/{len(rows)} rows")

    df["reply"] = replies
    df["expected_keys"] = expected_col
    df["answers"] = answers_col
    df["score"] = score_col
    df["matched_keys"] = matched_col
    df["missing_keys"] = missing_col

    write_table(df, str(out))

    elapsed = time.time() - t0
    elapsed_ms = int(elapsed * 1000)
    throughput = len(df) / elapsed if elapsed > 0 else 0.0
    avg_score = statistics.mean(results) if results else 0.0
    print(
        f"Processed {len(df)} rows, time={elapsed_ms} ms ({throughput:.1f} rows/sec), "
        f"average score={avg_score:.2f} -> {out}"
    )


if __name__ == "__main__":
    main()

