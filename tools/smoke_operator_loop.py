#!/usr/bin/env python3
"""One-shot operator loop: health check, seed demo intakes, process, sync drafts."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run(cmd: list[str]) -> None:
    print(f"[smoke] running: {' '.join(cmd)}")
    rc = subprocess.call(cmd)
    if rc != 0:
        print(f"[smoke] step failed (rc={rc})")
        sys.exit(rc)


def main() -> int:
    demo_from = Path("data/demo_inbox")
    replay_args = ["--into", "data/queue.db"]
    if demo_from.exists():
        replay_args = ["--from", str(demo_from), "--into", "data/queue.db"]
    run([sys.executable, "tools/doctor.py"])
    run([sys.executable, "tools/replay_intakes.py", *replay_args])
    run([sys.executable, "tools/triage_worker.py"])
    run([sys.executable, "tools/sync_drafts.py", "--limit", "10"])
    print("[smoke] completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
