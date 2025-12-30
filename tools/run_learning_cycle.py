#!/usr/bin/env python3
"""One-shot nightly learning cycle runner."""

from __future__ import annotations

import subprocess
import sys


def run(cmd: list[str]) -> None:
    print(f"--> Running: {' '.join(cmd)}")
    subprocess.check_call(cmd)


def main() -> int:
    # 1. Pull feedback from IMAP Sent (best-effort)
    try:
        run([sys.executable, "tools/watch_sent.py", "--limit", "100"])
    except Exception as exc:
        print(f"Sent watcher skipped or failed: {exc}")

    # 2. Rebuild golden dataset from closed-loop rows
    run([sys.executable, "tools/curate_golden_dataset.py"])

    # 3. Validate embedding availability
    run([sys.executable, "tools/preflight_check.py", "--embedding"])

    print("\nLearning cycle complete. New examples available for next triage run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
