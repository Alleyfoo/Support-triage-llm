# Legacy Chatbot Paths

This project pivoted to the Support Triage Copilot (`/triage/*`). The older chatbot flow (`/chat/*`, Excel-era docs) still exists but is not the primary path.

What remains:
- `/chat/enqueue` API
- Legacy docs under `docs/` (chat_queue_design, chat_runbook, etc.)
- Older tools/tests referencing the chatbot pipeline

How to treat it:
- Triaging is the default: use `/triage/enqueue`, worker `tools/triage_worker.py`, and the Streamlit review UI.
- Legacy pieces are kept for reference/backward compatibility. CI focuses on triage; load tests target `/triage/enqueue`.
- When updating, prefer the triage stack; only touch legacy if you explicitly need the chatbot demo.
