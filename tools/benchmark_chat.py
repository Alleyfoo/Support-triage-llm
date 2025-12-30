#!/usr/bin/env python3
"""Benchmark chat worker throughput on sample messages."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Dict, Iterable, List

import pandas as pd
import sys

SYS_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(SYS_ROOT))

from app.chat_service import ChatService
from tools import chat_dispatcher, chat_ingest, chat_worker

DEFAULT_MESSAGES: List[Dict[str, str]] = [
    {
        "conversation_id": "bench-1",
        "text": "Can you please tell me a joke about a chicken and a modular synthesizer?",
        "end_user_handle": "benchmark-user",
        "channel": "web_chat",
    },
    {
        "conversation_id": "bench-1",
        "text": "If a synthesizer is broken, what vst instrument would you suggest?",
    },
    {
        "conversation_id": "bench-2",
        "text": "Tell me about the loyalty program.",
    },
]




def _extract_replies(queue_path: Path) -> List[str]:
    if not queue_path.exists():
        return []
    df = pd.read_excel(queue_path)
    if df.empty or "response_payload" not in df.columns:
        return []
    replies: List[str] = []
    for payload in df["response_payload"]:
        if isinstance(payload, str):
            payload_strip = payload.strip()
            if not payload_strip:
                continue
            try:
                data = json.loads(payload_strip)
            except json.JSONDecodeError:
                data = {"content": payload_strip}
        elif isinstance(payload, dict):
            data = payload
        else:
            continue
        content = data.get("content")
        if content:
            replies.append(str(content))
    return replies
def _expand_messages(messages: Iterable[Dict[str, str]], repeat: int) -> List[Dict[str, str]]:
    expanded: List[Dict[str, str]] = []
    for _ in range(max(repeat, 1)):
        for message in messages:
            expanded.append(dict(message))
    return expanded


def run_benchmark(
    queue_path: Path,
    *,
    messages: Iterable[Dict[str, str]] = DEFAULT_MESSAGES,
    repeat: int = 1,
    dispatch: bool = False,
    dispatcher_id: str = "benchmark-dispatcher",
    transcript_path: Path | None = None,
) -> Dict[str, float]:
    """Ingest messages, run the chat worker until the queue is drained, and report timing."""

    queue_path.parent.mkdir(parents=True, exist_ok=True)
    payloads = _expand_messages(messages, repeat=repeat)
    if not payloads:
        raise ValueError("No messages supplied for benchmark")

    inserted = chat_ingest.ingest_messages(queue_path, payloads)
    chat_service = ChatService()

    processed = 0
    start = time.perf_counter()
    while chat_worker.process_once(queue_path, processor_id="benchmark-worker", chat_service=chat_service):
        processed += 1
    elapsed = time.perf_counter() - start
    replies = _extract_replies(queue_path)

    if dispatch:
        chat_dispatcher.dispatch_once(
            queue_path,
            dispatcher_id=dispatcher_id,
            adapter="web-demo",
            adapter_target=str(transcript_path or Path("data/chat_web_transcript.jsonl")),
        )
    throughput = processed / elapsed if elapsed > 0 else float("inf")
    return {
        "inserted": float(inserted),
        "processed": float(processed),
        "elapsed_seconds": elapsed,
        "messages_per_second": throughput,
        "replies": replies,
    }


def _load_messages(path: Path) -> List[Dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Benchmark JSON must contain a list of message objects")
    return [dict(item) for item in data]


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark the chat worker")
    parser.add_argument("--queue", default="data/benchmark_queue.xlsx", help="Queue workbook path")
    parser.add_argument("--messages-json", help="Path to JSON array of message objects")
    parser.add_argument("--repeat", type=int, default=1, help="Number of times to repeat the message set")
    parser.add_argument("--dispatch", action="store_true", help="Dispatch responses after the run")
    parser.add_argument("--reset", action="store_true", help="Remove existing queue before running")
    args = parser.parse_args()

    queue_path = Path(args.queue)
    if args.reset and queue_path.exists():
        queue_path.unlink()

    if args.messages_json:
        messages = _load_messages(Path(args.messages_json))
    else:
        messages = DEFAULT_MESSAGES

    metrics = run_benchmark(
        queue_path,
        messages=messages,
        repeat=args.repeat,
        dispatch=args.dispatch,
        transcript_path=Path("data/chat_web_transcript.jsonl") if args.dispatch else None,
    )

    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()



