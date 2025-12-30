> **Migration Note (2025-09-29):** The project is pivoting toward a queue-driven chatbot. See `docs/chat_queue_design.md` and `docs/chat_migration_plan.md` for the in-progress architecture while this document is rewritten for chat workflows.

## 1. Purpose & Scope
**Goal:** Provide a fully local pipeline that cleans incoming customer service emails, pre-fetches the keyed reference data the agent will need, and verifies that the canonical facts have been preserved. The system acts as the pre-processing stage ahead of any drafting or escalation so agents only see trustworthy, hallucination-free information.

**In scope**
- Single email API (`/reply`) + healthcheck that returns the cleaned draft, key data payload, and verification summary.
- Batch processing for CSV/Excel inputs containing raw customer emails.
- Local model (GGUF via llama.cpp) with "download if missing" helper for performing the text normalisation, plus a deterministic fallback stub.
- Automatic detection of lookup keys (explicit key codes or sender email) combined with optional `expected_keys` hints.
- Post-clean verification that ensures all requested key/value pairs appear verbatim in the cleaned draft.

**Out of scope (for now)**
- Sending replies or integrating with ticketing/CRM systems.
- Human-in-the-loop review UI beyond the structured JSON output.
- Multi-turn conversation memory or historical ticket correlation.
- Analytics dashboards beyond the verification metrics already returned.

## 2. Functional Requirements
- `run_pipeline(email_text, metadata=None)` â†’ returns:
  ```json
  {
    "reply": "<cleaned email draft>",
    "expected_keys": ["warranty_policy"],
    "answers": {"warranty_policy": "Our warranty covers every Aurora device for two full years."},
    "evaluation": {"score": 1.0, "matched": ["warranty_policy"], "missing": []}
  }
  ```
  - `reply` preserves the cleaned/enriched email text (field name retained for backwards compatibility).
  - `answers` holds the canonical key/value pairs fetched using the customer-provided key code or email address.
- Incoming metadata may include a subject line; if it starts with `Re:` (case-insensitive) the pipeline skips automated drafting and signals that the email should be escalated to a human agent.
  - `evaluation` summarises the verification pass that ensures the canonical values survived intact (no hallucinated substitutions).
  - Every invocation is appended to `data/pipeline_history.xlsx` (configurable via `PIPELINE_LOG_PATH`) capturing the original email, reply, expected keys, canonical answers, and verification metrics for downstream auditing.
- Lookup order:
  1. Honour any explicit key code found in the email body. Codes follow a case-insensitive `[A-Z]{2,}-\d{2,}` pattern (e.g. `AG-445`) and map to `key_code_<CODE>` entries in the knowledge base; these keys are inserted ahead of keyword matches and immediately seed the canonical `answers` payload.
  2. Fall back to the sender's email address when metadata supplies it.
  3. Use `expected_keys` hints from metadata or the request body to constrain which knowledge entries must appear.
- Regression flow exercises the ten curated emails in `data/test_emails.json`, sending each body through the pipeline (and LLM when configured) before aggregating verification scores.
- CLI: `python cli/clean_table.py <in.csv/xlsx> -o <out>` (outputs the cleaned draft, prefetched data, and verification metrics).
- CLI single file: `python -m cli.clean_file input.txt -o output.json` (writes the cleaned draft and verification summary for a single email).
- API: FastAPI `GET /healthz`, `POST /reply` with schemas defined in `app/schemas.py`.
- Knowledge template: Markdown table in `docs/customer_service_template.md` parsed at runtime; stores every canonical fact that may be stitched into an email.

## 3. Non-Functional Requirements
- Reproducibility: Dockerfile + requirements remain valid for offline execution.
- Deterministic fallback: when llama.cpp is unavailable, the stub must produce consistent cleaned drafts so automated tests can run without the model.
- Verification determinism: scoring logic must be pure, side-effect free, and independent of random seeds.
- Data integrity: any mismatch between the cleaned draft and canonical knowledge must be surfaced with a score below 1.0 and the offending key listed under `missing`.

## 4. Architecture & Directories
```
app/
  knowledge.py        # load & parse canonical key data
  pipeline.py         # orchestrate cleanup, enrichment, and verification
  slm_llamacpp.py     # llama.cpp wrapper / deterministic stub for rewriting emails
  evaluation.py       # (kept inside pipeline for now)
  config.py           # runtime knobs incl. knowledge template path
  server.py           # FastAPI application exposing /reply
  schemas.py          # request/response models (reply = cleaned email)
  model_download.py   # download-if-missing helper for GGUF models
cli/
  clean_table.py      # batch email cleaner & verifier
  clean_file.py       # single email cleaner
 data/
  test_emails.json    # 10 showcase emails with expected key data
 docs/
  customer_service_template.md  # canonical facts consumed during enrichment
```

Tests target the deterministic behaviour (no llama.cpp dependency) and validate verification scoring.

## 5. Knowledge Base
- Parsed from `docs/customer_service_template.md`.
- `KNOWLEDGE_SOURCE` can point to a Markdown/CSV/Excel file or HTTPS endpoint for live FAQ data; results are cached for `KNOWLEDGE_CACHE_TTL` seconds (set to `0` to refresh every call) and fall back to the template if unreachable.
- Stored as key/value pairs (e.g. `warranty_policy: â€¦`).
- Loader must raise if required entries referenced by the regression fixtures are missing.
- Knowledge entries drive both the enrichment step (facts inserted into the cleaned email) and the verification pass.

- Account-specific access keys live in `data/account_records.xlsx` (override with `ACCOUNT_DATA_PATH`). When metadata supplies a `customer_email`, only that customer's regular key is exposed to the agent. Secret keys remain hidden and trigger a security notice if referenced, and when the caller shares the correct secret themselves the pipeline emits an `account_identity_status` confirmation message instead of echoing the secret.

## 6. Email Cleanup & Data Enrichment
- If llama.cpp is available, use chat-completions with a fixed system instruction and deterministic user prompt:
  - **System prompt**
    ```
    You are Aurora Gadgets' pre-cleanup assistant. Normalise the customer email for the support team. Only use canonical data provided to you. Respond with JSON only.
    ```
  - **User prompt layout**
    ```
    You are preparing an email for internal agents.
    Customer email:
    <email body>

    Knowledge base:
    - company_name: Aurora Gadgets
    - founded_year: 1990
    ... (every key/value from docs/customer_service_template.md)

    Focus on confirming the keys: <expected list or "all relevant">.
    Return JSON in the following shape:<JSON>{"reply":"...","answers":{"key":"value"}}</JSON>
    ```
    The `<JSON>` / `</JSON>` sentinels guarantee the response contains a parseable block with the cleaned draft and `answers` map.
- Fallback stub constructs the cleaned draft by combining templated sentences per expected knowledge key so verification can match exact canonical values.
- Output structure must always include `reply` (cleaned draft string) and `answers` (dict of keyâ†’canonical value).

## 7. Verification
- `evaluate_reply(email_text, reply_text, expected_keys, knowledge)` computes:
  - `matched`: keys whose canonical values appear verbatim in the cleaned draft (case insensitive).
  - `missing`: expected keys whose values are absent or altered.
  - `score`: `len(matched) / len(expected_keys)` rounded to two decimals (defaults to 1.0 when no expectations).
- Verification runs immediately after enrichment inside `run_pipeline` so downstream systems never see hallucinated data.

## 8. Batch Processing
- CSV/Excel inputs may supply optional `expected_keys` column (pipe/semicolon separated).
- Output columns: `reply` (cleaned draft), `expected_keys`, `answers` (prefetched canonical data), `score`, `matched_keys`, `missing_keys`.
- Summary stats include average verification score across processed rows.

## 9. QA & Regression
- Unit tests cover key detection, scoring, deterministic stub drafts, and ensure the knowledge template contains required facts.
- `data/test_emails.json` contains exactly ten entries and is loaded in tests.
- Pytest runs without llama.cpp or external model downloads.

## 10. Roadmap
- Phase 1 (current): deterministic stub, knowledge-driven enrichment, verification metrics.
- Phase 2: richer normalisation templates, slot filling for customer names, config-driven verification thresholds.
- Phase 3: integration with actual llama.cpp models and external key-data services (CRM/OMS).
- Phase 4: optional web UI for reviewing cleaned drafts and auditing verification history.

## 11. Change Control
- Any future change must update the relevant sections of this document.
- Major scope or architecture adjustments require simultaneous documentation updates.

**Acceptance**
- File `docs/design_document.md` reflects the scope and behaviour described above.


## 11. Operational Resilience & Monitoring
- Two-node deployment: run the cleaner + Ollama on redundant Mac Minis (or comparable hosts) with mutual health checks. Only the primary drains the queue; if it fails, the standby promotes itself and resumes polling.
- Queue-driven backpressure: treat the email inbox or message bus as the source of truth. If all workers stop, messages remain queued until capacity returns—no emails are dropped.
- Health polling: schedule a 5-minute cron (or managed job) that hits `/healthz`, confirms the Ollama container responds, and verifies that recent jobs completed with `score == 1.0`. Escalate to on-call and pause queue draining on repeated failures.
- Quality gates: enforce regression-style spot checks after knowledge updates. The deterministic tests in `tests/` plus a curated template email with expected score `1.0` ensure policy changes don't regress coverage.
- Stateful data: the pipeline is intended to be stateless. The intake email, intermediate prompt, and final reply should remain in memory only. Logs written to `PIPELINE_LOG_PATH` are optional; if GDPR policy forbids storage, disable the file or redirect it to encrypted archival storage with rotation and automatic purging.
- Secrets & access: restrict `data/` and `docs/` directories to the service account. Secrets (regular/secret keys, dynamic FAQ) are loaded from Excel or network shares—ensure those shares enforce least-privilege and encrypt at rest.
- Disaster recovery: document the steps to recreate a node (clone repo, restore `.env`, reseed knowledge sources). Automated infra-as-code (e.g., Ansible, Terraform) can rebuild a node in minutes.

## 12. Security & Compliance
- The system never stores customer payloads beyond the existing queue/ticketing system; it processes data in-memory and emits sanitized replies.
- Maintain data-processing agreements: when reading from shared drives or SaaS APIs, ensure contracts cover automated processing.
- Log minimisation: avoid logging raw emails or secrets. If logs are required, scrub PII/anonymise before shipping to observability platforms.
- Audit trail: retain only the metadata necessary to prove the cleaning pipeline ran (timestamps, score, matched keys).
- Incident response: if a leak is suspected, revoke service credentials, rotate account key sheets, and review pipeline history to identify at-risk tickets.
- Regular penetration tests: exercise the prompt with jailbreak attempts (social engineering, secret key exfiltration) to ensure guardrails remain effective.




