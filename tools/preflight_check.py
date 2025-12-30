#!/usr/bin/env python3
"""Preflight environment and connectivity checks.

Run this before starting workers to verify required configuration and endpoints.
Exits non‑zero if any selected check fails.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List, Tuple


def _result(ok: bool, label: str, detail: str = "") -> Tuple[bool, str]:
    status = "PASS" if ok else "FAIL"
    line = f"[{status}] {label}"
    if detail:
        line += f" – {detail}"
    return ok, line


def check_ollama() -> List[str]:
    out: List[str] = []
    host = os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434"
    model = os.environ.get("OLLAMA_MODEL")
    backend = (os.environ.get("MODEL_BACKEND") or "").lower()
    if backend and backend != "ollama":
        ok, line = _result(True, f"MODEL_BACKEND={backend} (ollama check skipped)")
        out.append(line)
        return out
    ok, line = _result(bool(model), "OLLAMA_MODEL set", model or "unset")
    out.append(line)
    # Try simple GET to /api/tags
    try:
        import urllib.request
        with urllib.request.urlopen(host.rstrip("/") + "/api/tags", timeout=5) as resp:  # nosec - local endpoint
            ok_http = (200 <= resp.status < 300)
            ok, line = _result(ok_http, f"Reach Ollama at {host}", f"HTTP {resp.status}")
            out.append(line)
    except Exception as exc:  # pragma: no cover
        ok, line = _result(False, f"Reach Ollama at {host}", str(exc))
        out.append(line)
    return out


def check_knowledge_and_accounts() -> List[str]:
    out: List[str] = []
    knowledge = os.environ.get("KNOWLEDGE_SOURCE")
    accounts = os.environ.get("ACCOUNT_DATA_PATH")
    # Knowledge may fall back to template; if set, ensure file exists
    if knowledge:
        p = Path(knowledge)
        ok, line = _result(p.exists(), "KNOWLEDGE_SOURCE exists", str(p))
        out.append(line)
    else:
        out.append("[INFO] KNOWLEDGE_SOURCE not set (using template)")
    if accounts:
        pa = Path(accounts)
        ok, line = _result(pa.exists(), "ACCOUNT_DATA_PATH exists", str(pa))
        out.append(line)
    else:
        out.append("[INFO] ACCOUNT_DATA_PATH not set (using default data/account_records.xlsx)")
    return out


def check_imap() -> List[str]:
    out: List[str] = []
    host = os.environ.get("IMAP_HOST")
    user = os.environ.get("IMAP_USERNAME")
    pwd = os.environ.get("IMAP_PASSWORD")
    ok, line = _result(bool(host), "IMAP_HOST set", host or "unset")
    out.append(line)
    ok, line = _result(bool(user), "IMAP_USERNAME set", user or "unset")
    out.append(line)
    ok, line = _result(bool(pwd), "IMAP_PASSWORD set", "***" if pwd else "unset")
    out.append(line)
    return out


def check_smtp() -> List[str]:
    out: List[str] = []
    host = os.environ.get("SMTP_HOST")
    sender = os.environ.get("SMTP_FROM")
    recipient = os.environ.get("SMTP_TO")
    ok, line = _result(bool(host), "SMTP_HOST set", host or "unset")
    out.append(line)
    ok, line = _result(bool(sender), "SMTP_FROM set", sender or "unset")
    out.append(line)
    ok, line = _result(bool(recipient), "SMTP_TO set", recipient or "unset")
    out.append(line)
    return out


def check_paths() -> List[str]:
    out: List[str] = []
    for p in ("data", "docs", "tools"):
        ok, line = _result(Path(p).exists(), f"Path exists: {p}")
        out.append(line)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Preflight checks for cleanroom pipeline")
    ap.add_argument("--all", action="store_true", help="Run all checks")
    ap.add_argument("--ollama", action="store_true", help="Check Ollama connectivity and model env")
    ap.add_argument("--knowledge", action="store_true", help="Check knowledge and account data paths")
    ap.add_argument("--imap", action="store_true", help="Check IMAP env vars presence")
    ap.add_argument("--smtp", action="store_true", help="Check SMTP env vars presence")
    ap.add_argument("--paths", action="store_true", help="Check expected local paths exist")
    args = ap.parse_args()

    selected = args.all or not any(
        (args.ollama, args.knowledge, args.imap, args.smtp, args.paths)
    )

    lines: List[str] = []
    if args.ollama or selected:
        lines.extend(check_ollama())
    if args.knowledge or selected:
        lines.extend(check_knowledge_and_accounts())
    if args.imap or selected:
        lines.extend(check_imap())
    if args.smtp or selected:
        lines.extend(check_smtp())
    if args.paths or selected:
        lines.extend(check_paths())

    # Print and compute exit code
    failed = False
    for line in lines:
        print(line)
        if line.startswith("[FAIL]"):
            failed = True
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

