## v0.3.0

- Added dual time windows (customer vs investigation), surfaced in metadata, drafts, reports, and UI with friendly reasons.
- Enforced case-type tool gating in reports/drafts; webhook/HTTP cues now classify as integration instead of email delivery.
- UI: Case overview panel with windows, evidence summary, and gated Tier-3 escalate button; clearer service_status uncertainty wording.
- Documentation: added core contract and docs index; README links to contract.
- Security/tests: account_security uses generated XLSX fixture (no external file); regression tests for customer window sentences and dual-window propagation.
- Evidence metadata now carries both windows; draft/report guardrails ensure external-safe content only.
- Test status: `python -m pytest -q` → 112 passed, 13 skipped (intentional); warnings only (Pydantic v2 deprecations).
