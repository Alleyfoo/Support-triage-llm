# Test data samples

Purpose: lightweight fixtures for local triage/evidence testing without real customer data.

Contents:
- `fake_emails.jsonl` — inbound customer messages (id, tenant, subject, body, received_at).
- `email_events.jsonl` — email event evidence bundle shaped to the schema in DESIGN.md.
- `app_events.jsonl` — application event evidence bundle for non-email incidents.

Notes:
- Keep entries synthetic and PII-free.
- Align time windows/tenants between emails and events to make end-to-end tests deterministic.

CLI helper:
- `python tools/sample_data_smoke.py --summary` shows what’s available.
- `python tools/sample_data_smoke.py --init-queue --queue data/email_queue.xlsx --overwrite` seeds a queue workbook from the fake emails for quick pipeline smoke runs.
