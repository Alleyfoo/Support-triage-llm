# Support Triage Copilot — Single-User Tier-2 Console Plan (Agent Instructions)

Goal: deliver a local, single-user Tier-2 console that bundles ingest → triage → evidence → drafting → review/approve → learning feedback and is usable daily.

Principles: local-first; no auto-send; no multi-user auth for v1; no external SaaS deps. Streamlit preferred over Tkinter (already used; faster tables/JSON/charts).

Architecture:
- Components: FastAPI for enqueue/ops, `tools/triage_worker.py` for deterministic processing, Streamlit UI console.
- Storage: SQLite queue/artifacts; `data/` for attachments/exports; append-only audit log.

UI Screens:
- Queue Inbox: table (id/status/age/severity/case_type/tenant/last_updated); actions: open case, requeue/dead-letter, “process now.”
- Case Detail: original + redacted message; triage JSON; missing questions; suggested tools (run/rerun); evidence viewer; drafts (customer + escalation).
- Review & Outcomes: Approve (store review metadata + optional Drafts sync), Rewrite (store final + diff metrics), Escalate (mark case + export).
- Learning/Feedback: edit ratio, error tags, trends (weekly/monthly).

Operational UX:
- Setup wizard (first run): check Ollama reachable, IMAP valid (if enabled), SQLite writable, data folder exists.
- One-button local mode: `tools/run_local.py` to start API + worker (prints URLs); UI can be started separately.
- Data layout: `data/cases/<case_id>/` with `input.json`, `triage.json`, `evidence_bundle.json`, `final_report.json`, `drafts.json`, `review.json`, `audit_refs.json`.

Definition of done (v1):
- Operator ingests .eml, sees it in Queue, processes it, views triage/evidence, drafts, approves/rewrites/escalates, and feedback is recorded.
- No confusing pipeline-only dashboards unless explicitly in scope (cleanup plan governs).
