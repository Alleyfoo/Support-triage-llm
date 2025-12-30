#!/usr/bin/env python3
"""
Quick health check for the Headless Triage Bot.
Run this to see if the Daemon is alive and processing.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from app import config  # noqa: E402


def get_connection() -> sqlite3.Connection:
    return sqlite3.connect(config.DB_PATH)


def time_ago(iso_str: str | None) -> str:
    if not iso_str:
        return "Never"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = now - dt
        minutes = int(diff.total_seconds() / 60)
        if minutes < 60:
            return f"{minutes}m ago"
        hours = int(minutes / 60)
        if hours < 24:
            return f"{hours}h ago"
        return f"{int(hours/24)}d ago"
    except Exception:
        return "Unknown"


def main() -> int:
    if not Path(config.DB_PATH).exists():
        print(f"❌ Database not found at {config.DB_PATH}")
        return 1

    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT status, COUNT(*) as cnt FROM queue GROUP BY status")
    stats = {row["status"]: row["cnt"] for row in cur.fetchall()}
    queued = stats.get("queued", 0)
    drafted = stats.get("awaiting_human", 0)

    cur.execute("SELECT MAX(created_at) as last_ingest FROM queue")
    last_ingest = cur.fetchone()["last_ingest"]

    cur.execute("SELECT MAX(finished_at) as last_triage FROM queue WHERE status NOT IN ('queued')")
    last_triage = cur.fetchone()["last_triage"]

    cur.execute("SELECT MAX(closed_loop_at) as last_learn FROM queue")
    last_learn = cur.fetchone()["last_learn"]

    print(f"\n🤖 TriageBot Status [{datetime.now().strftime('%H:%M')}]")
    print("========================================")
    print(f"📥 Last Ingest:    {time_ago(last_ingest)}")
    print(f"🧠 Last Triage:    {time_ago(last_triage)}")
    print(f"🎓 Last Learn:     {time_ago(last_learn)}")
    print("----------------------------------------")
    print(f"Queue Depth:       {queued} pending")
    print(f"Drafts Waiting:    {drafted} ready for you")
    print("========================================")

    if queued > 10:
        print("⚠️  Warning: Queue is building up.")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
