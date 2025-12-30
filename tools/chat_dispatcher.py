#!/usr/bin/env python3
"""Stub dispatcher that acknowledges chat replies in the Excel queue."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from tools.process_queue import save_queue
from tools import chat_worker
from tools.chat_adapter_web import WebDemoAdapter


RESPONDED_STATUSES = {"responded", "handoff"}
PENDING_STATUSES = {"", "pending", "awaiting_dispatch"}


def _load_queue(queue_path: Path) -> pd.DataFrame:
    if not queue_path.exists():
        return chat_worker.ensure_chat_columns(pd.DataFrame())
    try:
        df = pd.read_excel(queue_path)
    except Exception as exc:  # pragma: no cover - operator feedback
        print(f"Warning: unable to read queue workbook {queue_path}: {exc}")
        return chat_worker.ensure_chat_columns(pd.DataFrame())
    return chat_worker.ensure_chat_columns(df)


def _pending_indices(df: pd.DataFrame) -> Iterable[int]:
    status_series = df["status"].astype(str).str.lower()
    delivery_series = df["delivery_status"].astype(str).str.lower()
    mask = status_series.isin(RESPONDED_STATUSES) & delivery_series.isin(PENDING_STATUSES)
    return df.index[mask]


def _parse_metadata(raw: object) -> dict:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw_metadata": raw}
    return {}


def _acknowledge_row(df: pd.DataFrame, idx: int, dispatcher_id: str, adapter_name: Optional[str]) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    df.loc[idx, "delivery_status"] = "sent"
    df.loc[idx, "status"] = "delivered"
    df.loc[idx, "processor_id"] = dispatcher_id
    metadata_obj = _parse_metadata(df.loc[idx, "response_metadata"])
    metadata_obj["dispatched_at"] = now_iso
    metadata_obj["dispatcher_id"] = dispatcher_id
    if adapter_name:
        metadata_obj["delivery_adapter"] = adapter_name
    df.loc[idx, "response_metadata"] = json.dumps(metadata_obj, ensure_ascii=False)


def _resolve_adapter(name: Optional[str], target: Optional[str]):
    if not name:
        return None
    normalised = name.lower()
    if normalised in {"web-demo", "web", "demo"}:
        return WebDemoAdapter(log_path=target)
    raise SystemExit(f"Unknown adapter '{name}'. Supported: web-demo")


def dispatch_once(
    queue_path: Path,
    dispatcher_id: str,
    *,
    adapter: Optional[str] = None,
    adapter_target: Optional[str] = None,
) -> int:
    df = _load_queue(queue_path)
    indices = list(_pending_indices(df))
    if not indices:
        return 0

    adapter_impl = _resolve_adapter(adapter, adapter_target)

    dispatched = 0
    for idx in indices:
        if adapter_impl is not None:
            row = df.loc[idx]
            adapter_impl.deliver(row)
        _acknowledge_row(df, idx, dispatcher_id, adapter)
        dispatched += 1

    save_queue(queue_path, df)
    print(f"Dispatched {dispatched} chat message(s) -> status=delivered")
    return dispatched


def main() -> None:
    parser = argparse.ArgumentParser(description="Acknowledge chat replies from the Excel queue")
    parser.add_argument("--queue", default="data/email_queue.xlsx", help="Queue workbook path")
    parser.add_argument("--dispatcher-id", default="chat-dispatcher-1", help="Identifier for this dispatcher instance")
    parser.add_argument("--adapter", default="web-demo", help="Delivery adapter to use (default: web-demo)")
    parser.add_argument(
        "--adapter-target",
        help="Optional adapter-specific target (e.g., output log path)",
    )
    parser.add_argument("--watch", action="store_true", help="Keep polling for responded rows")
    parser.add_argument("--poll-interval", type=float, default=3.0, help="Seconds between polls when --watch is set")
    args = parser.parse_args()

    queue_path = Path(args.queue)

    while True:
        dispatched = dispatch_once(
            queue_path,
            args.dispatcher_id,
            adapter=args.adapter,
            adapter_target=args.adapter_target,
        )
        if not args.watch:
            if dispatched == 0:
                print("No chat messages pending dispatch.")
            break
        if dispatched == 0:
            time.sleep(max(args.poll_interval, 0.25))
        else:
            time.sleep(0.5)


if __name__ == "__main__":
    main()
