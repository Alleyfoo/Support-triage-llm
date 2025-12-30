# Roadmap and Operational Design (Draft)

This document captures the next set of high‑value improvements, why they matter, and how we intend to implement them. It complements the Process Overview and Runbook by explaining what each “box” will do and how they connect.

## 1) Scheduling and Operations
- Goal: predictable, recoverable operation with one‑click start and scheduled tasks.
- Approach:
  - Windows Task Scheduler jobs:
    - Email ingest (IMAP or folder) every 1–5 minutes.
    - Queue worker as an "At startup" or "On logon" task (runs continuously).
    - Hourly direct benchmark to append CSV for trend charts.
  - Optional Docker Compose: ollama + worker + dashboard + (optional) ingest.
  - Preflight gate: `tools/preflight_check.py --all` before starting workers.
- Artifacts: Task XML templates and a `compose.yml` (future work).

## 2) Email Ingestion and Cleaning
- Why: raw emails often include HTML, signatures, and quoted threads that confuse models.
- Plan:
  - New module `app/email_preprocess.py` with:
    - html_to_text: strip HTML tags safely; preserve paragraphs and links text.
    - strip_signatures: heuristics (e.g., lines after `--`, common signature markers).
    - strip_quoted_replies: remove previous thread content (`On <date>`, `> quoted text`).
  - Wire into `tools/email_ingest.py` before enqueue. Keep original body in queue if needed for audit; enqueue a cleaned `body` by default.
  - Config flags to toggle cleaning steps.
- Testing: unit tests with representative .eml fixtures (multipart/HTML/UTF‑8/attachments).

## 3) Knowledge Freshness (FAQ Scraper)
- Why: FAQs change; we need low‑touch updates.
- Plan:
  - `tools/scrape_faq.py` fetches one or more URLs, extracts Q/A or key/value pairs, and writes `data/live_faq.xlsx` atomically.
  - Respect `Last‑Modified`/ETag. If unchanged, skip write. Keep a `data/live_faq.diff.json` summary for review.
  - Safe DOM extraction: CSS selectors configured in a YAML/JSON file to avoid code changes.
- Dashboard: show last fetch time, entry count, and whether the source changed.

## 4) LLM Benchmarks and TTFB
- Why: separate “time to first token” from “time to full reply”; watch cold‑start vs steady‑state.
- Plan:
  - Extend `tools/ollama_direct_benchmark.py` with `--stream` to measure:
    - first_token_ms (on first streamed chunk)
    - full_response_ms (on stream end)
  - Flag cold‑start on first call; record both.
  - Charts in Streamlit for TTFB vs full latency.

## 5) Outbound Approval Flow
- Status: `tools/send_drafts_smtp.py` sends drafts to a CS mailbox.
- Plan:
  - Simple approval tracker (CSV or Excel) with columns: id, decision (approved/reject), comments, decided_at.
  - `tools/send_approved.py` reads approvals and emails customers (To: original sender, CC/BCC: CS), then records a sent log.
  - No auto‑send by default; requires explicit approval entry.

## 6) Observability Enhancements
- Dashboard additions:
  - Backend/model banner (already printed in worker; surface in UI header).
  - Human‑review rate, last 24h p95 latency (from logs/queue).
  - Knowledge source freshness (mtime, count, last diff result).
- Hourly jobs append to CSVs to build trend charts automatically.

## 7) Compliance & SOPs (Initial Outline)
- Logging policy: production default is no content logging. If enabled, define lawful basis, retention, and access controls.
- Data access: account scoping by email; secret never in prompts; red‑team tests for non‑disclosure.
- SOP drafts to prepare:
  - Configuration & secrets management
  - Deployments & rollback
  - Knowledge updates & verification
  - Incident response & data deletion
- DPIA and ROPA entries to be completed.

## 8) Migration from Excel Queue (Future)
- Replace Excel with SQLite or a lightweight broker for atomic, concurrent updates.
- Benefits: lock safety, scale, easier multi‑agent processing.
- Transitional approach: keep Streamlit/demos working with the new backend behind a small queue abstraction.

## 9) Priorities (Suggested Order)
1. Email cleaner (biggest quality lift, low risk)
2. FAQ scraper + dashboard freshness indicators
3. TTFB streaming benchmark and charts
4. Approval tracker + send_approved demo (optional)
5. Task Scheduler XML / Compose for simplified ops
6. Queue backend hardening (SQLite) once demos stabilise

## 10) Multilingual Email Handling (Finnish & Swedish Focus)
- Goals:
  - Detect incoming language and normalise text (Finnish, Swedish, English as initial set).
  - Maintain per-language knowledge entries and account phrases.
  - Ensure replies are generated in the customer’s language and never mix languages unintentionally.

- Planned tasks:
  1. Language detection at ingest time using `langid` or fastText; store language code in the queue (`language` column) and pass through metadata.
  2. Extend knowledge loader to support language-specific sheets (e.g., `data/live_faq_fi.xlsx`, `data/live_faq_sv.xlsx`) or a combined sheet with `Key-Fi`, `Key-Sv` columns.
  3. Translate / curate core FAQ answers in Finnish and Swedish; ensure account phrases like “security notice” have approved translations.
  4. Update `detect_expected_keys` heuristics with Finnish/Swedish keyword lists; optionally train lightweight prompt templates per language.
  5. Model evaluation: test Ollama model’s Finnish/Swedish fluency; benchmark latency/quality vs English baseline. If quality is insufficient, explore multilingual models (e.g., Mixtral, LLaMA 3.1 multi) or add translation fallback via Helsinki-NLP OPUS-MT.
  6. Update Streamlit dashboard to show language mix over time (counts per language, human-review rate).
  7. Compliance review: confirm localisation preserves security controls (secret never disclosed) and ensure translations of security notices are approved by compliance/legal teams.

- Deliverables:
  - Multilingual knowledge files and keyword maps checked into `data/` (or documented source of truth).
  - Automated tests covering Finnish and Swedish emails for expected key detection, secret handling, and human-review routing.
  - Documentation updates (Process Overview & Runbook) describing language flow, translation responsibilities, and fallback behaviour.
