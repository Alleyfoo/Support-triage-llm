# Future Work & Enhancements

A non-exhaustive roadmap of improvements to expand the capabilities and robustness of the cleanroom email pipeline.

## 1. Integrations & Automation
- **Mailbox listeners:** Replace polling with push-based Graph subscriptions or IMAP IDLE listeners to reduce latency.
- **Ticketing adapters:** Build connectors for Zendesk, ServiceNow, or Salesforce to create/update tickets automatically.
- **CRM enrichment:** Call internal APIs to enrich replies with order status or loyalty-tier data.

## 2. Model Enhancements
- Swap the deterministic stub for a tuned small language model (distilled or fine-tuned on past tickets).
- Introduce retrieval-augmented generation (RAG) using Vector DB for high-volume FAQs.
- Add multi-lingual support by detecting language and handing off to locale-specific models.

## 3. Workflow & UI
- Build a lightweight review dashboard where agents can approve/reject replies before sending.
- Surface “confidence” indicators (based on score, missing keys, or heuristics) to prioritise human review.
- Offer inline suggestions for follow-up actions (e.g., create RMA, escalate to tier 2).

## 4. Reliability
- Introduce job orchestration (Airflow/Prefect) with retry policies and SLA tracking.
- Implement distributed locking so that multiple workers can safely share the queue.
- Capture structured tracing spans (OpenTelemetry) to debug latency hotspots.

## 5. Security & Compliance
- Automate secret rotation via HashiCorp Vault/Azure Key Vault.
- Add DLP scanning on replies to ensure no forbidden data leaves the system.
- Expand auditing to include hashed email identifiers for forensic purposes without exposing PII.

## 6. Knowledge Management
- Expose an admin UI for updating FAQs/account sheets with validation (no missing keys, duplicates).
- Version knowledge sources and support rollback to previous snapshots.
- Add change notifications so operators know when the knowledge base was modified.

## 7. Analytics & Feedback Loop
- Ingest downstream agent edits to measure how often humans override the pipeline’s replies.
- Use those signals to prioritise model retraining or FAQ updates.
- Provide monthly reports correlating volume, score, and staffing levels.

Update this list during quarterly planning; link accepted items to user stories in your backlog.
