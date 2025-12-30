# Chat Service Migration Plan

## Objectives
- Transition the "email pre-cleaner" pipeline into a conversational assistant that handles inbound chat traffic across multiple channels.
- Reuse the repository layout and queue path while modernising data structures, workers, and dispatch tooling for multi-turn messages.
- Preserve compliance guardrails and auditability while enabling faster responses and richer conversational context.

## Phased Milestones
1. **Foundations (Week 1)**
   - Finalise queue schema changes (`docs/chat_queue_design.md`) and write a migration script for existing workbooks.
   - Ship the static chat demo at `ui/chat_demo.html` for stakeholder walkthroughs (Load Transcript button reads dispatcher output).
   - Land the initial chat worker at `tools/chat_worker.py` so turns run through the Excel queue.
   - Provide a CLI intake stub at `tools/chat_ingest.py` to drop demo turns into the queue.
   - Deliver the migration CLI at `tools/migrate_queue_chat.py` for existing email workbooks.
   - Stub new intake/dispatcher CLIs (`tools/chat_ingest.py`, `tools/chat_dispatcher.py`) that operate on the Excel queue.
   - Expose a temporary webhook via FastAPI (`POST /chat/enqueue`) for web widget prototypes.
   - Add feature flags so legacy email ingestion can be toggled off per environment.
2. **Pipeline Adaptation (Week 2)**
   - Teach `app.pipeline.run_pipeline` to accept conversation context + metadata (channel, tags).
   - Extend guardrails/evaluator logic to produce structured `response_payload` decisions (`answer`, `clarify`, `handoff`).
   - Evolve `app/chat_service.py` into the primary orchestration layer that feeds the queue and dispatcher.
   - Pair the worker with a lightweight dispatcher prototype to prove end-to-end delivery (log to `data/chat_web_transcript.jsonl` via the web-demo adapter).
   - Iterate on `tools/chat_dispatcher.py` to support channel-specific delivery adapters.
   - Update regression fixtures in `tests/` to cover chat messages and escalation cases.
3. **Channel Integrations (Week 3)**
   - Implement initial channel adapters (e.g., web widget stub + Slack) with secrets loaded via config.
   - Build dispatcher retry + dead-letter handling using queue status fields.
   - Instrument telemetry for latency, delivery status, and handoff rates.
4. **Operational Readiness (Week 4)**
   - Refresh runbooks, onboarding, and observability docs for chat terminology and flows.
   - Provide agent-facing guidance for handoff workflows and conversation monitoring.
   - Conduct load tests with synthetic conversations; validate failover + recovery steps.

## Key Workstreams
- **Queue & Tooling:** migrate schema, port ingestion to channel connectors, add dispatcher with per-channel plug-ins.
- **Pipeline:** support context windows, multilingual handling, and guardrail-driven escalation logic.
- **Knowledge & NLU:** repurpose knowledge base to surface short-form KB answers and conversation tags.
- **Ops & Compliance:** ensure transcripts/audit logs comply with existing data-protection rules; update DPA references.
- **UX & Analytics:** adapt UI dashboards (`ui/monitor.py`) to display conversation backlogs, response SLAs, and handoff metrics.

## Risks & Mitigations
- **Excel bottleneck:** concurrency limits may surface sooner with rapid chat traffic; plan a fast follow to move the queue into SQLite/Postgres once prototypes stabilise.
- **Channel variance:** each platform has different delivery semantics; abstract adapters and isolate secrets/configs.
- **Context drift:** without disciplined history management the bot may hallucinate; cap context length and add regression suites for long threads.
- **Human takeover:** ensure the queue clearly signals handoff state and freezes automated responders to avoid double replies.

## Tracking & Next Steps
- Use the repo issue tracker to file implementation tasks under labels `migration` and `chat`.
- Stand up a weekly sync to review queue metrics, dispatcher errors, and pipeline quality scores.
- Archive email-specific docs/tests once the chat stack reaches parity.


