#!/usr/bin/env python3
"""Quick environment/health checks for the triage copilot."""

from __future__ import annotations

import os
import socket
import sqlite3
from pathlib import Path
from typing import List, Tuple

from app import config


def _check_db() -> Tuple[bool, str]:
    try:
        Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(config.DB_PATH, timeout=5)
        conn.execute("SELECT 1")
        conn.close()
        return True, f"SQLite OK ({config.DB_PATH})"
    except Exception as exc:
        return False, f"DB error: {exc}"


def _check_ollama() -> Tuple[bool, str]:
    host = config.OLLAMA_HOST
    try:
        parsed = host.replace("http://", "").replace("https://", "")
        parts = parsed.split(":")
        host = parts[0]
        port = int(parts[1]) if len(parts) > 1 else 11434
        with socket.create_connection((host, port), timeout=2):
            return True, f"Ollama reachable at {config.OLLAMA_HOST}"
    except Exception as exc:
        return False, f"Ollama not reachable ({config.OLLAMA_HOST}): {exc}"


def _check_imap_env() -> Tuple[bool, str]:
    if not os.environ.get("IMAP_HOST"):
        return False, "IMAP not configured (IMAP_HOST missing)"
    required = ["IMAP_USERNAME", "IMAP_PASSWORD"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        return False, f"IMAP missing env: {', '.join(missing)}"
    return True, "IMAP env present"


def _check_demo_inbox() -> Tuple[bool, str]:
    path = Path("data/demo_inbox")
    return path.exists(), f"Demo inbox folder {'found' if path.exists() else 'missing'} at {path}"


def _check_internal_drafts() -> Tuple[bool, str]:
    enabled = (os.environ.get("SYNC_INTERNAL_ANALYSIS") or "0").lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return True, "Internal drafts disabled (SYNC_INTERNAL_ANALYSIS=0)"
    to_addr = os.environ.get("IMAP_INTERNAL_TO")
    if not to_addr:
        return False, "SYNC_INTERNAL_ANALYSIS=1 but IMAP_INTERNAL_TO not set"
    return True, "Internal drafts enabled with IMAP_INTERNAL_TO set"


def main() -> int:
    checks: List[Tuple[bool, str]] = [
        _check_db(),
        _check_ollama(),
        _check_demo_inbox(),
        _check_internal_drafts(),
    ]
    if os.environ.get("IMAP_HOST"):
        checks.append(_check_imap_env())

    ok = True
    for passed, msg in checks:
        status = "OK" if passed else "FAIL"
        print(f"[{status}] {msg}")
        if not passed:
            ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
