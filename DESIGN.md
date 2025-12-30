# Support Triage Copilot — Design

## System intent (what this must never do)
- No auto-send. Everything is drafts + human review.
- No unsupported claims. If evidence lacks “bounce/DMARC/auth fail”, the report must not claim it.
- No promises/ETAs in drafts.
- Redact PII before LLM; store redacted view by default.

## Processing stages
1) **Ingress**: `/triage/enqueue` (API key). Inputs: text, tenant hint, source. Helpers: `.eml` importer, Intercom-like importer.
2) **Queue**: SQLite (volume-mounted). Deterministic row claim + idempotency keys + backoff-aware retries with dead-letter on max retries.
3) **Triage**: heuristic or LLM → triage JSON (schema-validated) + draft reply + missing questions. Redaction happens first.
4) **Tool selection**: rules/LLM-hinted; allowlist only.
5) **Evidence bundles**: run tools → evidence_bundle schema.
6) **Final report**: template or LLM → final_report schema. Claim-checker strips unsupported claims; fallback to template if needed.
7) **Review UI**: triage JSON + evidence + report; approve/rewrite/escalate; exports package.

## Contracts (schemas on disk)
- `schemas/triage.schema.json`: case_type, severity, time_window, scope, symptoms, examples, missing_info_questions, suggested_tools, draft_customer_reply.
- `schemas/evidence_bundle.schema.json`: source (email_events/app_events/integration_events/dns_checks), time_window, tenant, summary_counts, events[] {ts,type,id,message_id,detail}, optional metadata.
- `schemas/final_report.schema.json`: classification {failure_stage, confidence, top_reasons}, timeline_summary, customer_update, engineering_escalation, kb_suggestions.

## Tool protocol
- Allowlist registry (`tools/registry.py`): each tool has params_schema, result_schema, fn.
- Worker validates tool name + params, executes, validates result → stores evidence_json + sources run.
- Adding a tool: define params schema + result schema, implement fn, register in REGISTRY.

## Claim-check rule
- Report text is scanned for keywords (bounce/quarantine/DMARC/SPF/rate limit/auth failed/workflow disabled). Each claim must be backed by evidence events/counts/metadata. Otherwise warnings are added and LLM report is repaired or template fallback is used.

## Prompts/behavior constraints
- Triage: extract only what’s in text; if timeframe unclear, ask; valid JSON only; no invention.
- Report: use evidence only; cite event IDs/timestamps; no ETAs; if missing evidence, say so and ask for it.

## Storage/audit
- Queue rows store: raw + redacted payload, triage JSON, evidence JSON, final report JSON, tool execution list, meta (model, prompt version, mode, schema_valid, claim warnings), retry_count/available_at, timestamps, case_id/message_id.
- Retention: `tools/retention.py` can purge/scrub via RETENTION_* envs; compose runs it on container start.
