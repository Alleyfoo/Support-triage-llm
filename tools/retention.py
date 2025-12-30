#!/usr/bin/env python3
"""Retention and scrubbing helper for queue DB."""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import config


def _now() -> datetime:
    return datetime.now(timezone.utc)


def purge(db_path: Path, days: int) -> int:
    cutoff = (_now() - timedelta(days=days)).isoformat().replace("+00:00", "Z")
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM queue WHERE created_at < ?", (cutoff,))
        deleted = cursor.rowcount or 0
        conn.commit()
        return deleted
    finally:
        conn.close()


def scrub_raw(db_path: Path, days: int) -> int:
    cutoff = (_now() - timedelta(days=days)).isoformat().replace("+00:00", "Z")
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE queue SET raw_payload = '', payload = redacted_payload WHERE created_at < ? AND redacted_payload != ''",
            (cutoff,),
        )
        updated = cursor.rowcount or 0
        conn.commit()
        return updated
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Purge or scrub old queue rows")
    parser.add_argument("--db", default=config.DB_PATH, help="Path to queue DB")
    parser.add_argument("--purge-days", type=int, help="Delete rows older than N days")
    parser.add_argument("--scrub-days", type=int, help="Scrub raw payloads older than N days (keep redacted)")
    args = parser.parse_args()

    db_path = Path(args.db)
    if args.purge_days:
        deleted = purge(db_path, args.purge_days)
        print(f"Deleted {deleted} rows older than {args.purge_days} days")
    if args.scrub_days:
        updated = scrub_raw(db_path, args.scrub_days)
        print(f"Scrubbed {updated} rows older than {args.scrub_days} days")


if __name__ == "__main__":
    main()
