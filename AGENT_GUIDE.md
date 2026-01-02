# Support Triage Copilot — Agent Guide

Start here:
- Follow `README.md` → Quick start to launch the daemon (Docker or `python tools/daemon.py`).
- Check health with `python tools/status.py`; triage runs against the SQLite queue by default.
- Drafts sync to your IMAP Drafts folder; nothing auto-sends. Keep the Internal Ref footer intact.

Daily workflow:
- Ingest: use `/triage/enqueue` (or `tools/ingest_eml.py`) to drop customer messages into the queue.
- Triage: `tools/triage_worker.py` processes queued items, suggests tools, and drafts replies.
- Review: edit drafts in your mail client; sent-mail feedback is ingested by the daemon for learning.

More detail:
- Design rails and schemas: `DESIGN.md`, `schemas/*.json`
- Runbook and guardrails: `RUNBOOK.md`, `docs/specs/FEEDBACK_LOOP.md`
- Legacy chat/Excel flows are archived under `legacy/` with docs in `docs/legacy/`.
