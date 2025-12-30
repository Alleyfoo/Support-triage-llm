#!/usr/bin/env python3
"""Sample data helper for manual smoke runs.

- Prints the synthetic email + evidence fixtures.
- Optionally seeds an Excel queue (data/email_queue.xlsx) using the fake emails.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from tools.process_queue import init_queue  # type: ignore


SAMPLES_DIR = ROOT / "tests" / "data_samples"


def load_fake_emails(limit: int | None = None) -> List[dict]:
    path = SAMPLES_DIR / "fake_emails.jsonl"
    emails: List[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            emails.append(json.loads(line))
            if limit and len(emails) >= limit:
                break
    return emails


def load_jsonl(name: str) -> List[dict]:
    path = SAMPLES_DIR / f"{name}.jsonl"
    rows: List[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            rows.append(json.loads(line))
    return rows


def seed_queue(queue_path: Path, limit: int | None = None, overwrite: bool = False) -> None:
    emails = load_fake_emails(limit=limit)
    dataset = []
    for email in emails:
        dataset.append(
            {
                "id": email["id"],
                "customer": email.get("tenant"),
                "subject": email.get("subject"),
                "body": email.get("body", ""),
                "raw_body": email.get("body", ""),
                "language": "en",
                "language_source": "synthetic",
                "ingest_signature": "sample_data_v1",
                "expected_keys": [],
            }
        )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, dir=str(queue_path.parent)) as tmp:
        json.dump(dataset, tmp, ensure_ascii=False, indent=2)
        tmp_path = Path(tmp.name)

    try:
        init_queue(queue_path, tmp_path, overwrite=overwrite)
    finally:
        tmp_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Work with sample triage fixtures")
    parser.add_argument("--summary", action="store_true", help="Print a short summary of sample emails and evidence")
    parser.add_argument("--init-queue", action="store_true", help="Seed an Excel queue from fake emails")
    parser.add_argument("--queue", default="data/email_queue.xlsx", help="Queue path to create when using --init-queue")
    parser.add_argument("--limit", type=int, help="Limit number of fake emails to load/seed")
    parser.add_argument("--overwrite", action="store_true", help="Allow overwriting an existing queue file")
    args = parser.parse_args()

    if args.summary or not args.init_queue:
        emails = load_fake_emails(limit=args.limit)
        email_events = load_jsonl("email_events")
        app_events = load_jsonl("app_events")
        print(f"Loaded {len(emails)} fake emails, {len(email_events)} email event bundles, {len(app_events)} app event bundles from {SAMPLES_DIR}")
        if emails:
            sample = emails[0]
            print(f"Example email: tenant={sample.get('tenant')} subject={sample.get('subject')} received_at={sample.get('received_at')}")
        if email_events:
            print(f"Email evidence window: {email_events[0]['time_window']}")
        if app_events:
            print(f"App evidence window: {app_events[0]['time_window']}")

    if args.init_queue:
        queue_path = Path(args.queue)
        queue_path.parent.mkdir(parents=True, exist_ok=True)
        seed_queue(queue_path, limit=args.limit, overwrite=args.overwrite)
        print(f"Queue ready at {queue_path}")


if __name__ == "__main__":
    main()
