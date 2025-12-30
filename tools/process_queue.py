
#!/usr/bin/env python3
"""Excel-backed queue processor for the cleanroom pipeline."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.pipeline import run_pipeline
from app.config import MODEL_BACKEND, OLLAMA_MODEL, OLLAMA_HOST


QUEUE_COLUMNS = [
    "id",
    "customer",
    "subject",
    "body",
    "raw_body",
    "language",
    "language_source",
    "language_confidence",
    "ingest_signature",
    "expected_keys",
    "status",
    "agent",
    "started_at",
    "finished_at",
    "latency_seconds",
    "score",
    "matched",
    "missing",
    "reply",
    "answers",
]

STRING_COLUMNS = [
    "customer",
    "subject",
    "body",
    "raw_body",
    "language",
    "language_source",
    "expected_keys",
    "status",
    "agent",
    "started_at",
    "finished_at",
    "matched",
    "missing",
    "reply",
    "answers",
    "ingest_signature",
]
NUMERIC_COLUMNS = ["latency_seconds", "score", "language_confidence"]


def init_queue(queue_path: Path, dataset_path: Path, *, overwrite: bool = False) -> None:
    if queue_path.exists() and not overwrite:
        raise SystemExit(f"Queue file already exists: {queue_path}. Use --overwrite to replace it.")
    if not dataset_path.exists():
        raise SystemExit(f"Dataset not found: {dataset_path}")

    data = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit("Dataset must be a list of email objects")

    rows: List[dict] = []
    for item in data:
        rows.append(
            {
                "id": item.get("id"),
                "customer": item.get("customer"),
                "subject": item.get("subject"),
                "body": item.get("body", ""),
                "raw_body": item.get("body", ""),
                "language": item.get("language", ""),
                "language_source": item.get("language_source", ""),
                "language_confidence": item.get("language_confidence"),
                "ingest_signature": item.get("ingest_signature", ""),
                "expected_keys": json.dumps(item.get("expected_keys", []), ensure_ascii=False),
                "status": "queued",
                "agent": "",
                "started_at": "",
                "finished_at": "",
                "latency_seconds": None,
                "score": None,
                "matched": "",
                "missing": "",
                "reply": "",
                "answers": "",
            }
        )

    df = pd.DataFrame(rows, columns=QUEUE_COLUMNS)
    save_queue(queue_path, df)
    print(f"Queue initialised with {len(df)} emails -> {queue_path}")


def load_queue(queue_path: Path) -> pd.DataFrame:
    if not queue_path.exists():
        raise SystemExit(f"Queue file not found: {queue_path}. Run with --init-from to create it.")
    try:
        df = pd.read_excel(queue_path)
    except Exception as exc:
        print(f"Warning: unable to read queue workbook {queue_path}: {exc}")
        print("Returning empty queue view. If this persists, reinitialise the queue file.")
        df = pd.DataFrame(columns=QUEUE_COLUMNS)
    for column in STRING_COLUMNS:
        if column not in df.columns:
            df[column] = ""
        df[column] = df[column].astype("object").where(df[column].notna(), "")
    for column in NUMERIC_COLUMNS:
        if column not in df.columns:
            df[column] = pd.NA
    return df


def save_queue(queue_path: Path, df: pd.DataFrame) -> None:
    """Atomically write the queue workbook to reduce risk of corruption."""
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    import tempfile, os
    # Write to a temp file in the same directory, then replace
    with tempfile.NamedTemporaryFile(mode="w+b", suffix=".xlsx", delete=False, dir=str(queue_path.parent)) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with pd.ExcelWriter(tmp_path, engine="openpyxl", mode="w") as writer:
            df.to_excel(writer, index=False, sheet_name="queue")
        os.replace(tmp_path, queue_path)
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


def _parse_expected_keys(raw: object) -> List[str]:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return []
    if isinstance(raw, list):
        return [str(item) for item in raw]
    text = str(raw).strip()
    if not text:
        return []
    try:
        value = json.loads(text)
        if isinstance(value, list):
            return [str(item) for item in value]
    except json.JSONDecodeError:
        pass
    return [part.strip() for part in text.split("|") if part.strip()]


def process_once(queue_path: Path, agent_name: str) -> bool:
    df = load_queue(queue_path)
    status_series = df.get("status")
    if status_series is None:
        queued_mask = pd.Series(True, index=df.index)
    else:
        queued_mask = status_series.astype(str).str.lower().isin(["", "nan", "queued"])
    queued_indices = df.index[queued_mask]
    if queued_indices.empty:
        return False

    idx = queued_indices[0]
    timestamp = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    df.loc[idx, "status"] = "processing"
    df.loc[idx, "agent"] = agent_name
    df.loc[idx, "started_at"] = timestamp
    save_queue(queue_path, df)

    row = df.loc[idx]
    body = row.get("body", "")
    if not body and row.get("raw_body"):
        body = row.get("raw_body", "")
    expected_keys = _parse_expected_keys(row.get("expected_keys"))
    metadata: Dict[str, object] = {}
    if expected_keys:
        metadata["expected_keys"] = expected_keys
    language = str(row.get("language", "")).strip()
    if language:
        metadata["language"] = language

    start = time.perf_counter()
    result = run_pipeline(str(body), metadata=metadata or None)
    elapsed = time.perf_counter() - start

    evaluation = result.get("evaluation", {}) or {}
    matched = evaluation.get("matched", [])
    missing = evaluation.get("missing", [])

    df.loc[idx, "finished_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    df.loc[idx, "latency_seconds"] = round(elapsed, 4)
    df.loc[idx, "score"] = evaluation.get("score")
    df.loc[idx, "matched"] = json.dumps(matched, ensure_ascii=False)
    df.loc[idx, "missing"] = json.dumps(missing, ensure_ascii=False)
    df.loc[idx, "reply"] = result.get("reply", "")
    df.loc[idx, "answers"] = json.dumps(result.get("answers", {}), ensure_ascii=False)

    if result.get("human_review"):
        df.loc[idx, "status"] = "human-review"
        message = f"Escalated email #{row.get('id')} for human review"
    else:
        df.loc[idx, "status"] = "done"
        message = f"Processed email #{row.get('id')} -> score={evaluation.get('score')} latency={elapsed:.3f}s"

    save_queue(queue_path, df)
    print(message)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Process an Excel-backed email queue")
    parser.add_argument("--queue", default="data/email_queue.xlsx", help="Queue workbook path")
    parser.add_argument("--init-from", help="Seed the queue from a JSON dataset and exit")
    parser.add_argument("--overwrite", action="store_true", help="Allow overwriting an existing queue when initialising")
    parser.add_argument("--agent-name", default="agent-1", help="Identifier for this worker")
    parser.add_argument("--watch", action="store_true", help="Keep polling for new queued items")
    parser.add_argument("--poll-interval", type=float, default=5.0, help="Seconds between polls when --watch is set")
    args = parser.parse_args()

    queue_path = Path(args.queue)

    if args.init_from:
        init_queue(queue_path, Path(args.init_from), overwrite=args.overwrite)
        return

    if MODEL_BACKEND == "ollama":
        print(f"Backend: ollama model={OLLAMA_MODEL or '(unset)'} host={OLLAMA_HOST}")
    else:
        print(f"Backend: {MODEL_BACKEND}")

    while True:
        processed = process_once(queue_path, args.agent_name)
        if not processed:
            if args.watch:
                time.sleep(max(args.poll_interval, 0.1))
                continue
            print("Queue empty. Nothing to process.")
            break


if __name__ == "__main__":
    main()
