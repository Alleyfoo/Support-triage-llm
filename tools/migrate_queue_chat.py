#!/usr/bin/env python3
"""Utility to migrate legacy email queue workbooks into the chat schema."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Iterable, Optional
from uuid import uuid4

import pandas as pd

from tools import chat_worker
from tools.process_queue import save_queue


STATUS_MAP = {
    "": "queued",
    "nan": "queued",
    "queued": "queued",
    "processing": "processing",
    "done": "responded",
    "human-review": "handoff",
    "failed": "failed",
    "responded": "responded",
}


def _normalise_expected_keys(raw: object) -> str:
    if raw in (None, "", [], ()):  # type: ignore[comparison-overlap]
        return json.dumps([], ensure_ascii=False)
    if isinstance(raw, list):
        return json.dumps(raw, ensure_ascii=False)
    text = str(raw).strip()
    if not text:
        return json.dumps([], ensure_ascii=False)
    try:
        value = json.loads(text)
        if isinstance(value, list):
            return json.dumps(value, ensure_ascii=False)
    except json.JSONDecodeError:
        pass
    parts = [segment.strip() for segment in text.split("|") if segment.strip()]
    return json.dumps(parts, ensure_ascii=False)


def _normalise_json(raw: object, *, fallback_empty: bool = True) -> str:
    if raw in (None, ""):
        return json.dumps([], ensure_ascii=False) if fallback_empty else ""
    if isinstance(raw, (dict, list)):
        return json.dumps(raw, ensure_ascii=False)
    text = str(raw)
    if not text:
        return json.dumps([], ensure_ascii=False) if fallback_empty else ""
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        return json.dumps([text], ensure_ascii=False) if fallback_empty else json.dumps({"raw": text}, ensure_ascii=False)


def _response_payload(reply: object) -> str:
    if not reply:
        return ""
    text = str(reply).strip()
    if not text:
        return ""
    return json.dumps({"type": "text", "content": text}, ensure_ascii=False)


def _response_metadata(row: pd.Series) -> str:
    metadata: Dict[str, object] = {}
    answers = row.get("answers")
    if answers:
        try:
            metadata["answers"] = json.loads(answers) if isinstance(answers, str) else answers
        except (TypeError, json.JSONDecodeError):
            metadata["answers_raw"] = answers
    score = row.get("score")
    if pd.notna(score):
        metadata["score"] = float(score)
    latency = row.get("latency_seconds")
    if pd.notna(latency):
        metadata["latency_seconds"] = float(latency)
    if not metadata:
        return ""
    metadata["migrated_from"] = "email_queue"
    return json.dumps(metadata, ensure_ascii=False)


def migrate_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, row in df.iterrows():
        new_row: Dict[str, object] = {}
        message_id = row.get("message_id") or row.get("id") or uuid4()
        conversation_id = row.get("conversation_id") or row.get("ingest_signature") or message_id
        new_row["message_id"] = str(message_id)
        new_row["conversation_id"] = str(conversation_id)
        new_row["end_user_handle"] = str(row.get("end_user_handle") or row.get("customer") or "")
        new_row["channel"] = str(row.get("channel") or "web_chat")
        new_row["message_direction"] = "inbound"
        new_row["message_type"] = "text"
        payload = row.get("payload") or row.get("body") or row.get("raw_body") or ""
        new_row["payload"] = str(payload)
        new_row["raw_payload"] = str(row.get("raw_payload") or row.get("raw_body") or "")
        new_row["language"] = str(row.get("language") or "")
        new_row["language_source"] = str(row.get("language_source") or "")
        lang_conf = row.get("language_confidence")
        new_row["language_confidence"] = float(lang_conf) if pd.notna(lang_conf) else None
        new_row["conversation_tags"] = _normalise_expected_keys(row.get("expected_keys"))
        status_raw = str(row.get("status") or "").lower()
        new_status = STATUS_MAP.get(status_raw, "queued")
        new_row["status"] = new_status
        new_row["processor_id"] = str(row.get("agent") or row.get("processor_id") or "")
        new_row["started_at"] = str(row.get("started_at") or "")
        new_row["finished_at"] = str(row.get("finished_at") or "")
        latency = row.get("latency_seconds")
        new_row["latency_seconds"] = float(latency) if pd.notna(latency) else None
        score = row.get("score")
        new_row["quality_score"] = float(score) if pd.notna(score) else None
        new_row["matched"] = _normalise_json(row.get("matched"))
        new_row["missing"] = _normalise_json(row.get("missing"))
        new_row["response_payload"] = _response_payload(row.get("reply"))
        new_row["response_metadata"] = _response_metadata(row)
        new_row["delivery_route"] = str(row.get("delivery_route") or "")
        if new_status == "responded":
            new_row["delivery_status"] = "pending"
        elif new_status == "handoff":
            new_row["delivery_status"] = "blocked"
        else:
            new_row["delivery_status"] = str(row.get("delivery_status") or "")
        new_row["ingest_signature"] = str(row.get("ingest_signature") or "")
        rows.append(new_row)

    new_df = pd.DataFrame(rows)
    new_df = chat_worker.ensure_chat_columns(new_df)
    return new_df


def migrate_queue(input_path: Path, output_path: Path, *, overwrite: bool = False) -> Path:
    if output_path.exists() and not overwrite:
        raise SystemExit(f"Output queue already exists: {output_path}. Use --overwrite to replace it.")
    try:
        df = pd.read_excel(input_path)
    except FileNotFoundError:
        raise SystemExit(f"Source queue not found: {input_path}")
    except Exception as exc:
        raise SystemExit(f"Unable to read {input_path}: {exc}")

    migrated = migrate_dataframe(df)
    save_queue(output_path, migrated)
    print(f"Migrated {len(migrated)} row(s) -> {output_path}")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate legacy email queue workbook to chat schema")
    parser.add_argument("--queue", default="data/email_queue.xlsx", help="Source queue workbook path")
    parser.add_argument("--output", default="data/chat_queue.xlsx", help="Destination workbook path")
    parser.add_argument("--overwrite", action="store_true", help="Allow overwriting the destination file")
    args = parser.parse_args()

    migrate_queue(Path(args.queue), Path(args.output), overwrite=args.overwrite)


if __name__ == "__main__":
    main()
