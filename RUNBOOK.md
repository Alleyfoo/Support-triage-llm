# Support Triage Copilot — Runbook

## 9) Guardrails and security checklist
- PII redaction happens before LLM calls.
- All tool outputs stored for audit.
- LLM outputs stored and versioned.
- No auto-send; UI requires human approval.
- Tool allowlist enforced and parameters validated.
- Secrets via env vars; never stored in DB.
- Safe mode default: sample logs only.

## 10) Quality gates (how we know it works)
### 10.1 Unit tests
- Redaction removes emails/phones reliably.
- JSON schemas validate (triage + final report).
- Tool allowlist rejects unknown tools.

### 10.2 Scenario tests (golden files)
Create tests/scenarios/ with:
- inbound email text
- sample logs
- expected triage JSON fields
- expected questions to ask
- expected classification stage

### 10.3 Human evaluation rubric
For each run:
- Did it extract timeframe/scope correctly?
- Are the questions minimal and useful?
- Is the summary evidence-linked?
- Would a customer trust the draft?
- Would engineering act on the escalation?

## 11) Milestones (ship in slices)
Milestone A — Triage-only (1-2 days)
- Triage JSON + customer draft
- PII redaction
- Review UI shows triage + draft

Milestone B — Evidence-from-samples (2-4 days)
- Tool registry + sample evidence bundles
- Timeline summary + escalation draft
- Scenario tests

Milestone C — Real connectors (later)
- Intercom export ingestion (or webhook)
- Email provider events (if available)
- App logs (read-only)
- Linear draft output format

Milestone D — Support ops features (later)
- Ticket clustering / spike detection
- KB suggestion pipeline
- Metrics dashboard (CSAT proxy: reopen rate, time-to-first-signal)

Milestone E — Learning loop (gated)
- Metrics-only mode (safe): compute contradiction rate, redundant questions, claim warnings, routing accuracy from existing artifacts.
- Dataset mode (requires approval): redacted triage/report/evidence summaries with retention/access controls. Default OFF.
- See `docs/MILESTONE_E_LEARNING_LOOP.md` for policy gates and rollout.

## 12) Developer workflow (simple rules)
- Keep the rails stable: queue/worker contract should not break.
- Add new tools via tools/registry.py + schema + tests.
- Add new scenarios before new features.
- Prefer small PRs: one tool + one scenario + one UI display.

## 13) What "done" means for v1
Given an inbound message like "Emails are not arriving" with no details:
- The system returns a structured triage JSON.
- Asks targeted questions (time window, scope, recipient domains).
- Drafts a customer reply that does not overpromise.
- Produces a list of suggested evidence tools to run next.
