#!/usr/bin/env python3
"""Lightweight supervisor to run ingest/triage/sync/feedback/learning on a schedule."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import schedule

from tools import email_ingest, triage_worker, sync_drafts, watch_sent, run_learning_cycle

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("daemon")


def job_ingest() -> None:
    log.info("[Daemon] Ingesting emails...")
    host = os.environ.get("IMAP_HOST")
    user = os.environ.get("IMAP_USERNAME")
    pwd = os.environ.get("IMAP_PASSWORD")
    if not (host and user and pwd):
        log.info("IMAP env not set; skipping ingest.")
        return
    queue_path = Path(os.environ.get("EMAIL_QUEUE_PATH") or "data/email_queue.xlsx")
    try:
        email_ingest.ingest_imap(queue_path, clean=True, retain_raw=True, detect_keys=True)
    except Exception as exc:
        log.exception("Ingest failed: %s", exc)


def job_triage() -> None:
    log.info("[Daemon] Processing queue...")
    try:
        processed = triage_worker.process_once("daemon-worker")
        if processed:
            job_sync_drafts()
    except Exception as exc:
        log.exception("Triage failed: %s", exc)


def job_sync_drafts() -> None:
    log.info("[Daemon] Syncing drafts to IMAP...")
    try:
        sync_drafts.sync_drafts(limit=20)
    except Exception as exc:
        log.exception("Sync drafts failed: %s", exc)


def job_watch_sent() -> None:
    log.info("[Daemon] Checking Sent items for feedback...")
    try:
        watch_sent.watch_sent(
            lookback_hours=int(os.environ.get("IMAP_SENT_LOOKBACK_HOURS") or 24),
            limit=int(os.environ.get("IMAP_SENT_LIMIT") or 200),
            dry_run=False,
        )
    except Exception as exc:
        log.exception("Watch sent failed: %s", exc)


def job_learning() -> None:
    log.info("[Daemon] Running nightly learning cycle...")
    try:
        run_learning_cycle.main()
    except Exception as exc:
        log.exception("Learning cycle failed: %s", exc)


def main() -> int:
    schedule.every(1).minutes.do(job_ingest)
    schedule.every(5).seconds.do(job_triage)
    schedule.every(10).minutes.do(job_watch_sent)
    schedule.every().day.at("03:00").do(job_learning)

    log.info("TriageBot Daemon Started. Press Ctrl+C to stop.")
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Daemon stopped by user.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
