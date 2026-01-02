# Support Triage Copilot — Future Improvements & Analytics Roadmap (Agent Instructions)

Goal: improve capability without breaking local-first, single-operator usability. Add analytics only when they answer real ops questions.

Principles:
- Prefer aggregated metrics; avoid storing raw sensitive text.
- Make riskier retention/export opt-in.
- Every metric must map to an action.

Phase 1: Ops-facing analytics
- Metrics: time-to-draft/resolution, edit ratio, redundant questions, claim-check warnings, tool usage frequency. Show last 7/30 days + per-case drilldowns.
- Ops health panel: queue backlog, dead-letter count, retry rate, median processing latency, draft quality indicators (edit ratio trend).
- Avoid resurrecting XLSX “pipeline history” dashboards—serve charts from SQLite.

Phase 2: Safe learning (default ON)
- Learn routing/prompt tuning from reviewer tags + edit ratios (no raw text).
- Maintain local “golden cases” (redacted summaries + outcomes).
- Dataset mode (default OFF, gated): export redacted training JSONL only when explicitly enabled with retention policy.

Phase 3: Intelligence upgrades
- Case clustering/spike detection (local embeddings) for incident detection.
- KB suggestion improvements when repeated fixes appear; store minimal redacted templates.

Phase 4: Optional connectors (plugin style)
- Read-only log tailer; provider events import; Intercom/Zendesk parsers. All under `tools/connectors/` with schema-defined outputs.

Engineering upgrades:
- Versioned contracts folder; scenario packs under `tests/scenarios/`; repro bundle exporter for escalations.

Definition of done:
- A single Ops Console shows whether the system is improving (fewer rewrites, fewer redundant questions, faster drafts).
- No analytics depends on legacy subsystems unless explicitly supported.
