# Legacy Chatbot Paths

This project pivoted to the Support Triage Copilot (`/triage/*`). The older chatbot + Excel queue flows still exist for historical reference but are not the primary path.

What remains (archived):
- Chatbot + Excel queue code now lives under `legacy/chat` and `legacy/excel_queue`.
- Legacy docs were moved to `docs/legacy/` (chat queue design/runbook/migration, archived runbooks/design docs, templates).
- Legacy tests are parked in `legacy/tests` and are not part of the main suite.

How to treat it:
- Core product scope: triage copilot (SQLite queue + worker + Streamlit UI).
- Pipeline subsystem is an optional extension (enable with `FEATURE_PIPELINE=1`); not required for daily Tier-2 ops.
- Only touch the `legacy/` tree if you explicitly need the old chat/Excel demo; keep production paths on the SQLite-backed triage stack.
