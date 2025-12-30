import argparse
import time
import random
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import os
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from app.pipeline import run_pipeline
from app.io_utils import parse_terms


MAX_RETRIES = 3


def _process_row(row):
    text = str(row.get("text", ""))
    terms = parse_terms(row.get("protected_terms")) if "protected_terms" in row else []
    translate = bool(row.get("translate_embedded", False))

    start = time.perf_counter()
    retries = 0
    while True:
        try:
            res = run_pipeline(text, translate_embedded=translate, protected_terms=terms)
            break
        except Exception:
            retries += 1
            if retries >= MAX_RETRIES:
                res = {"flags": [{"type": "error"}], "clean_text": text, "changes": []}
                break
    end = time.perf_counter()
    return (end - start), retries, res.get("flags", [])


def main():
    ap = argparse.ArgumentParser(description="Benchmark the cleaning pipeline")
    ap.add_argument("--file", required=True, help="Input CSV/Excel file with text column")
    ap.add_argument("--workers", type=int, default=1, help="Number of worker threads")
    ap.add_argument("--samples", type=int, default=200, help="Number of rows to sample")
    args = ap.parse_args()

    df = pd.read_csv(args.file) if Path(args.file).suffix.lower().endswith(".csv") else pd.read_excel(args.file)
    n = args.samples
    if n > len(df):
        sampled = df.sample(n=n, replace=True, random_state=random.randint(0, 1_000_000))
    else:
        sampled = df.sample(n=n, random_state=random.randint(0, 1_000_000))

    rows = sampled.to_dict("records")

    latencies = []
    total_retries = 0
    flag_counter = Counter()

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(_process_row, r) for r in rows]
        for fut in as_completed(futures):
            dur, retries, flags = fut.result()
            latencies.append(dur)
            total_retries += retries
            for f in flags:
                if isinstance(f, dict):
                    flag_counter[f.get("type", "?")] += 1
                else:
                    flag_counter[str(f)] += 1
    t1 = time.perf_counter()

    if not latencies:
        print("No rows processed")
        return

    lat_ms = [l * 1000 for l in latencies]
    lat_ms.sort()
    median = lat_ms[len(lat_ms)//2] if len(lat_ms)%2==1 else 0.5*(lat_ms[len(lat_ms)//2-1] + lat_ms[len(lat_ms)//2])
    p95_index = min(len(lat_ms)-1, int(len(lat_ms)*0.95))
    p95 = lat_ms[p95_index]

    total_time = t1 - t0
    throughput = len(lat_ms) / total_time if total_time > 0 else float('inf')
    retry_rate = total_retries / len(lat_ms)

    print(f"median latency: {median:.1f} ms")
    print(f"95p latency: {p95:.1f} ms")
    print(f"throughput: {throughput:.2f} rows/sec")
    print(f"JSON-retry rate: {retry_rate*100:.1f}%")
    if flag_counter:
        print("flag distribution:")
        for k, v in flag_counter.items():
            print(f"  {k}: {v}")
    else:
        print("flag distribution: none")


if __name__ == "__main__":
    main()
