# Support Triage Copilot — Agent Guide Book

This guide is split for quick navigation:
- DESIGN.md — system intent, rails, schemas, tools, and prompt rules.
- RUNBOOK.md — guardrails, quality gates, milestones, workflow, and definition of done.

Quick orientation:
- Local-first, auditable support triage copilot (deterministic queue/worker; allowlisted tools; PII redaction; audit trail).
- Outputs: structured triage JSON, evidence-backed timelines, customer + escalation drafts (human-reviewed before send).
- Tooling: deterministic retrieval, LLM for narration; allowlist only; minimal data with redaction-first.

See DESIGN.md and RUNBOOK.md for the full details.
