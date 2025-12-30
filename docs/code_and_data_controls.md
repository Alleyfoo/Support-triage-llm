# Code and Data Controls: Initial Plan (GxP‑style)

This plan outlines how code, configuration, and data handling will be specified and controlled for a privacy‑first customer service assistant. It serves as a starting point for formal GxP/ISO documentation and can be expanded into SOPs.

## 1. Scope & Components
- Application modules
  - `app/knowledge.py`: dynamic FAQ/knowledge loader (Excel/CSV/Markdown/HTTP, TTL caching)
  - `app/pipeline.py`: core orchestration, guardrails, identity checks, logging
  - `app/slm_*.py`: model backends (ollama, llama.cpp)
  - `tools/*`: ingestion, processing, benchmarking, monitoring support scripts
  - `ui/monitor.py`: Streamlit dashboard (non‑production monitoring)
- Data areas
  - `data/`: demo queue, optional history, benchmark logs
  - External: IMAP mailbox (intake), SMTP mailbox (drafts), live FAQ file or URL

## 2. Data Classes & Access Rules
- Customer email content (transient): processed in memory for generation. Not stored by default.
- Account records (per sender): Excel file with scoped fields. Only non‑secret fields are exposed to the model. Secret fields are never sent to the model.
- Knowledge base (non‑PII): FAQ content and public info. May be loaded from Excel/CSV/Markdown/HTTP.
- Metrics (non‑content): latencies, status, scores. Retained for monitoring.
- Optional content logs (restricted): only if `PIPELINE_LOG_PATH` is set and policy allows. Apply retention and access controls.

## 3. Data Flow Controls
- Ingestion (IMAP/folder) → Queue (Excel for demo; plan for broker/DB in prod)
- Worker loads knowledge + account scope → generates draft via local model
- Drafts sent to CS mailbox for human approval
- No automatic outbound emails to customers in default mode

## 4. Identity & Authorization
- Account scoping by sender email
- Optional identity verification via pre‑shared secret: verified if the exact secret appears in the email text; secret is never disclosed
- Banned keys enforced in pipeline to prevent secret leakage

## 5. Configuration Management
- Environment variables define backend, model, knowledge source, cache TTL, paths
- Configuration recorded alongside code releases; changes tracked via Git
- Sensitive settings (credentials) in secrets manager or env injection; not committed

## 6. Logging & Retention
- Default: no content logging; set `PIPELINE_LOG_PATH=""`
- If enabled, logs are written atomically to reduce corruption
- Metrics logs (CSV/XLSX) intended for monitoring only; apply retention policies

## 7. Change Control
- All code changes via PR with code review
- Semantic commit messages; link changes to tickets/tasks
- Versioned releases with release notes summarising changes, risks, and rollback steps

## 8. Validation & Testing
- Unit tests covering dynamic knowledge reload, subject routing, queue processing, security behaviour
- Benchmarks to validate real model latencies vs stub
- Manual E2E demos using generator → ingest → worker → SMTP drafts

## 9. Operational Controls
- Health checks for backend availability
- Monitoring dashboard for queue depth, latencies, human‑review counts
- Scheduled jobs for periodic benchmarks; alerts on p95 regressions

## 10. Risk & Mitigations (Initial)
- Data leakage: enforce account scoping, banned keys, human review, secret never exposed
- Model unavailability: fallback to deterministic stubs for demos; alert in production
- File corruption: atomic writes for history and queue; recover by reinitialising
- Misconfiguration: banner/log of active backend/model; preflight checks in workers

## 11. Next Steps (To‑Do)
- Replace Excel queue with SQLite/broker for concurrency & locking
- Add SMTP “send approved reply to customer” with approval workflow and audit trail
- Formal SOPs for configuration, deployments, incident response, data deletion
- DPIA and ROPA entries; define lawful basis for any optional content logging

