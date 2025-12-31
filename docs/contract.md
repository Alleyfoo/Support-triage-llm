## Support Triage Contract (core objects + invariants)

Read this if you're confused or changing tools/triage behavior. It is the spine, not a museum piece.

- **Intake (immutable envelope)**  
  - Stores raw subject/body/from/received_at. Subject/body are never mutated. Status workflow: new → investigating → awaiting_customer → escalated → resolved.

- **Time windows**  
  - `customer_time_window`: parsed from message (start/end may be partial), with `reason`, `confidence`, `anchor` = received_at.  
  - `investigation_time_window`: policy window used for evidence queries (often last 24h, anchored to received_at).  
  - UI/metadata show friendly reasons; do not surface “default_no_date” to customers.

- **Evidence run**  
  - Tool + params; stores `summary_external` (customer-safe only) and internal result.  
  - Metadata includes both windows, query reason, tool_name, cache, checked_at.  
  - Tools may run only if allowed by case-type policy.

- **Case-type policy (tool allowlist)**  
  - incident → {log_evidence, service_status}  
  - email_delivery → {email_events, dns_email_auth_check_sample, log_evidence when outage language}  
  - Others: minimal unless outage language triggers log_evidence.

- **Draft / customer-facing text**  
  - Built only from `summary_external` and safe metadata; never from internal blobs.  
  - If a customer window exists, include “Customer reports issues since/between …”.  
  - Evidence sentence uses investigation/log evidence.

- **Handoff pack**  
  - Structured payload with evidence refs (IDs + tool params), never raw blobs.  
  - Includes intake_id, identity_confidence, service_ids, time_window used, evidence_refs, next steps, export_version.

- **Reports**  
  - Use only allowed evidence for the final case_type.  
  - Timeline/updates may include customer window + observed window; avoid certainty language when status=unknown.
