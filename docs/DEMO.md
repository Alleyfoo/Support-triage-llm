# Support Triage Copilot – Demo Script

## Prereqs
- Python 3.11+, docker (if using ollama container), Ollama model pulled (e.g., llama3.1:8b).
- Env (PowerShell example):
  set OLLAMA_URL=http://localhost:11434
  set OLLAMA_MODEL=llama3.1:8b

## One-command run (LLM mode)
python tools/one_run.py --triage-mode llm --ollama-url http://localhost:11434 --ollama-model llama3.1:8b
- Seeds fake emails (incl. angry rant)
- Drains worker
- Writes inbox preview + .eml/.json under data/demo_run/<timestamp>/
- Runs learning metrics into data/demo_run/<timestamp>/learning/

Artifacts to open:
- INBOX_PREVIEW--llama3-1-8b.md
- data/demo_run/<ts>/learning/learning_report.md (routing, redundancies, contradictions)
- per-case JSON under data/demo_run/<ts>/rows/

## Heuristic mode (no LLM)
python tools/one_run.py --triage-mode heuristic --skip-tests

## Streamlit review UI (optional)
streamlit run ui/app.py
- Auth via STREAMLIT_AUTH_USER/PASS if set
- Approve/Rewrite/Escalate writes review_final_* , diff ratios, tags.

## API quick check
curl -X POST http://localhost:8000/triage/enqueue -H "X-API-Key: $INGEST_API_KEY" -d '{"text":"Emails to contoso.com bouncing"}'

## Evidence + routing sanity
- email_delivery → fetch_email_events_sample + dns_email_auth_check_sample
- integration → fetch_integration_events_sample
- auth_access → fetch_app_events_sample

## Learning metrics standalone
python tools/learning_report.py --db-path data/demo_queue.sqlite --out-dir data/learning/manual_run

## Feedback dataset export (gated)
LEARNING_MODE=dataset python tools/export_feedback_dataset.py --db-path data/demo_queue.sqlite --out data/learning/export_feedback.jsonl
- Fails if unredacted emails or raw_payload/raw_text found.

## What to point out in a live demo
- Triage JSON: case_type, scope, reported_time_window vs time_window, missing-info questions are conditional.
- Draft reply: no severity text; questions adapt to supplied time/domains; angry case uses short targeted asks.
- Evidence: synthetic bundles with IDs/timestamps; claim warnings prevented by schema + claim checker.
- Final report: references evidence IDs; routing matches case_type.
- Learning: report highlights redundant questions, routing accuracy, tag counts, edit ratios.
