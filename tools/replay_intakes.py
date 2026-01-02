#!/usr/bin/env python3
"""Replay demo intakes into the SQLite queue for smoke tests or demos."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List

from app import queue_db
from app.connectors.demo import DemoConnector, demo_paths


def _load_items(paths: List[Path], limit: int | None) -> list:
    connector = DemoConnector(paths)
    items = list(connector.pull())
    return items[:limit] if limit else items


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay demo intakes into the queue.")
    parser.add_argument("--paths", nargs="+", help="Directories or files with demo inputs (.txt/.eml)")
    parser.add_argument("--limit", type=int, help="Optional cap on number of items")
    parser.add_argument("--db-path", help="Override DB_PATH for this run")
    parser.add_argument("--into", dest="db_path_alias", help="Alias for --db-path")
    parser.add_argument("--from", dest="from_paths", nargs="+", help="Alias for --paths")
    args = parser.parse_args()

    target_db = args.db_path or args.db_path_alias
    if target_db:
        import os
        os.environ["DB_PATH"] = target_db

    queue_db.init_db()

    source_paths = args.paths or args.from_paths
    targets = [Path(p) for p in source_paths] if source_paths else demo_paths()
    items = _load_items(targets, args.limit)
    if not items:
        print("No demo intakes found.")
        return

    inserted = 0
    for item in items:
        payload = {
            "text": item.text,
            "end_user_handle": item.tenant or "",
            "channel": "demo",
            "message_direction": "inbound",
            "message_type": "text",
            "raw_payload": "",
            "ingest_signature": (item.source_meta or {}).get("source", ""),
            "case_id": (item.source_meta or {}).get("source", ""),
            "received_at": item.received_at.isoformat() if item.received_at else None,
        }
        queue_db.insert_message(payload)
        inserted += 1

    print(f"Inserted {inserted} demo intake(s) into the queue.")


if __name__ == "__main__":
    main()
