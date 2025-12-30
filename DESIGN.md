# Support Triage Copilot — Design

## 0) What this project is
A local-first, auditable support triage copilot that turns messy inbound customer messages into:
1) Structured triage data (timeframe, scope, symptoms, missing info)
2) Evidence-backed timeline summaries (from allowed log sources)
3) Drafts: customer reply + internal escalation (e.g., Linear-ready)

This is not an autonomous agent. It is a human-verified assistant running inside secure rails:
- deterministic pipeline (queue/worker)
- allowlisted tools only
- PII redaction
- audit trail of inputs/outputs

## 1) The rails we keep (non-negotiables)
- **Ingress**: API endpoint enqueues jobs (API key auth)
- **Queue**: SQLite (transaction-safe row claiming; multi-worker safe)
- **Worker**: processes jobs deterministically (no agent wandering)
- **LLM**: local inference (Ollama) or pluggable provider later
- **Outputs**: stored in DB + review UI (no auto-send)
- **Observability**: every job has a traceable run record

## 2) The new focus (what changes)
We are not building a chatbot.
We are building support operations augmentation:
- Extract context from messy customer text
- Pull evidence from known sources (logs/events)
- Summarize into "what happened / where it failed / next steps"
- Draft communications and escalations consistently

## 3) Core principles (how the agent must behave)
### 3.1 Evidence > vibes
The agent may suggest hypotheses, but:
- facts must come from tool outputs
- summaries must reference event IDs/timestamps if available

### 3.2 Deterministic retrieval, probabilistic narration
- Scripts/tools retrieve facts deterministically.
- The LLM narrates/summarizes/drafts based on those facts.

### 3.3 Minimal data + redaction-first
- Redact PII before the model sees content (emails, names, phone numbers).
- Only pass the minimum log slice needed.

### 3.4 Human in the loop
- The system produces drafts.
- Humans approve before sending to customers or creating engineering tickets.

### 3.5 Allowlist only
The agent can only call tools that we define:
- no arbitrary SQL
- no arbitrary shell
- no network calls unless explicitly enabled

## 4) System map (mental model)

Customer msg/email  
   |  
   v  
[Ingest API] -> (SQLite queue) -> [Worker]  
                                |   \  
                                |    -> [Tools: log fetchers, analyzers]  
                                |  
                                -> [LLM: parse + summarize + draft]  
                                |  
                                -> (SQLite outputs + audit)  
                                |  
                                -> [Review UI] -> Human sends / escalates  

## 5) Job types
We start with one job type, but design for extension.

### 5.1 TRIAGE job (v1)
Input: raw customer message (email/ticket text)  
Output:
- triage JSON (structured fields + missing info checklist)
- draft customer reply (asks for missing details)
- suggested tool runs (what evidence to fetch next)

### 5.2 TRIAGE+EVIDENCE job (v2)
Input: raw message + selected evidence sources (time window, tenant)  
Output:
- timeline summary (what happened)
- failure stage classification (trigger/provider/recipient/integration/etc.)
- draft customer reply (with status + next actions)
- internal escalation draft (Linear-ready)

## 6) Data contracts (schemas)
### 6.1 Triage schema (LLM output must match this)
```json
{
  "case_type": "email_delivery | integration | ui_bug | data_import | access_permissions | unknown",
  "severity": "critical | high | medium | low",
  "time_window": {
    "start": "ISO-8601 or null",
    "end": "ISO-8601 or null",
    "confidence": 0.0
  },
  "scope": {
    "affected_tenants": ["string"],
    "affected_users": ["string"],
    "affected_recipients": ["string"],
    "recipient_domains": ["string"],
    "is_all_users": false,
    "notes": "string"
  },
  "symptoms": ["string"],
  "examples": [
    {"recipient": "string|null", "timestamp": "ISO-8601|null", "description": "string"}
  ],
  "missing_info_questions": ["string"],
  "suggested_tools": [
    {"tool_name": "string", "reason": "string", "params": {"k":"v"}}
  ],
  "draft_customer_reply": {
    "subject": "string",
    "body": "string"
  }
}
```

### 6.2 Evidence bundle schema (tool output)
Tools must return JSON like:
```json
{
  "source": "email_events | app_events | integration_events | dns_checks",
  "time_window": {"start":"...", "end":"..."},
  "tenant": "string|null",
  "summary_counts": {"sent":0,"bounced":0,"deferred":0,"delivered":0},
  "events": [
    {"ts":"ISO","type":"string","id":"string","message_id":"string|null","detail":"string"}
  ]
}
```

### 6.3 Final report schema (LLM output)
```json
{
  "classification": {
    "failure_stage": "trigger | queue | provider | recipient | configuration | unknown",
    "confidence": 0.0,
    "top_reasons": ["string"]
  },
  "timeline_summary": "string",
  "customer_update": {
    "subject": "string",
    "body": "string",
    "requested_info": ["string"]
  },
  "engineering_escalation": {
    "title": "string",
    "body": "string",
    "evidence_refs": ["event_id/timestamp/message_id"],
    "severity": "S1|S2|S3",
    "repro_steps": ["string"]
  },
  "kb_suggestions": ["string"]
}
```

## 7) Tooling design (allowlisted tools)
Tools are plain Python functions with strict schemas.

### 7.1 Required v1 tools
- redact_pii(text) -> {redacted_text, redaction_map_meta}
- detect_time_window(text) -> {start,end,confidence}
- extract_domains(text) -> {domains:[...]}
- load_sample_logs(time_window, tenant?) -> evidence_bundle (until real logs exist)

### 7.2 Required v2 tools (when real sources exist)
- fetch_email_events(time_window, tenant?, recipient_domain?)
- fetch_app_events(time_window, tenant?, workflow_id?)
- fetch_integration_events(time_window, tenant?, integration_name?)
- dns_email_auth_check(domain) (SPF/DKIM/DMARC presence only; no external calls unless allowed)

### 7.3 Tool calling policy
The LLM never executes tools directly.  
The worker executes tools based on:
- allowlist validation
- parameter validation
- optional human approval flags (for sensitive tools)

## 8) LLM prompts (behavior constraints)
### 8.1 "Triage Parser" prompt rules
- Extract only what is in the text; do not invent.
- If timeframe is unclear, ask questions instead of guessing.
- Output must be valid JSON matching the triage schema.

### 8.2 "Evidence Summarizer" prompt rules
- Use only evidence bundle content for facts.
- If evidence is missing, say what is missing.
- Produce: classification, timeline summary, drafts.

### 8.3 Draft-writing rules
Customer drafts must:
- be calm, professional, short
- ask 2-4 targeted questions
- state what we checked + what is next
- avoid promising ETAs

Escalation drafts must:
- include tenant/time window
- list exact examples (IDs/timestamps)
- expected vs actual
- impact statement
