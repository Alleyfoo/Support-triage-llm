# Legacy code and tests

This folder archives non-core flows so the main repo stays focused on the SQLite-backed triage pipeline.

- `chat/`: Excel-backed chatbot demo (ingest/worker/dispatcher, web transcript demo, notebooks). Tests live in `legacy/tests/chat/`.
- `excel_queue/`: Original Excel email queue worker, IMAP/folder ingest, approval/drafts senders, and monitor UI. Tests live in `legacy/tests/excel/`.
- `tests/`: Archived pytest cases for the above flows; not collected by the main suite.

These artifacts are kept for reference only and are not maintained for production. Prefer the SQLite queue (`tools/triage_worker.py`) and docs in `README.md`, `DESIGN.md`, and `RUNBOOK.md`.
