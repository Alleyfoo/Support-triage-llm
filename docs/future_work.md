# Future Work (Milestone D scope)

This section captures the planned Support Ops features once the triage + evidence pipeline is stable.

## Clustering / Spike Detection
- Group similar cases by symptom/domain/tool outputs to detect spikes.
- Sliding window counts + thresholds; surface alerts in UI.
- Always keep human review; no auto-bulk actions.

## KB Pipeline
- Suggest KB updates from recurring classifications and escalation drafts.
- Export suggested articles with evidence references for CS/Docs to review.
- Keep model prompts tied to KB version; store provenance.
- Placeholder doc: `docs/kb_pipeline.md`

## Metrics & Observability
- Core metrics: time-to-first-signal, reopen rate, bounce rate per domain, tool success rate.
- Dashboards fed from SQLite/Postgres; no PII in metrics.
- Keep audit trail: case_id, tools executed, evidence refs, approvals.

## Connectors (real sources)
- Intercom/API exports (read-only), email provider events, app logs (read-only), Linear draft payloads.
- Keep existing evidence schema; connectors populate bundles with IDs/timestamps for receipts.

## Security & Ops
- UI auth by default; per-case retention policies (raw vs redacted).
- Configurable correlation IDs everywhere (API → worker → exports).

## Guardrails (unchanged)
- No auto-send; drafts only.
- Schema validation + claim checker remain mandatory.
- Allowlist tools; LLM suggestions are hints only.
