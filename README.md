# Support Triage Copilot (Milestone A demo)

Quickstart:
- `cp .env.example .env` (set `INGEST_API_KEY` if you want)
- `docker compose up --build`
- Enqueue: `curl -X POST http://localhost:8000/triage/enqueue -H "Content-Type: application/json" -H "X-API-KEY: ${INGEST_API_KEY}" -d '{"text":"Emails are bouncing to contoso.com","tenant":"acme"}'`
- UI: http://localhost:8501 (approve / rewrite / escalate)

Services:
- `api` (FastAPI) — `/triage/enqueue` + `/triage/run`
- `worker` — `tools/triage_worker.py --watch` writes triage JSON + draft to SQLite
- `ui` — Streamlit review console for triage + drafts
- `ollama` — local model endpoint (used when TRIAGE_MODE=llm)
- Retention: `tools/retention.py` runs on container start (set `RETENTION_PURGE_DAYS`/`RETENTION_SCRUB_DAYS` in `.env`)
- Idempotency/retries: ingestion hashes text+tenant to dedupe; worker retries with backoff then dead-letters after `MAX_RETRIES`
- UI auth: set `STREAMLIT_AUTH_USER`/`STREAMLIT_AUTH_PASS` to gate the console

API surfaces:
- Primary: `/triage/*` (current demo path)
- Legacy: `/chat/*` still exists for earlier chatbot flow, but triage is the focus and load tests now target `/triage/enqueue`.

One-run demo (tests + triage worker + inbox preview):
```
python tools/one_run.py
# LLM mode and ensure model is pulled locally
python tools/one_run.py --triage-mode llm --ollama-model llama3.1:8b --ollama-url http://localhost:11434 --ensure-ollama-model
# Exports live under data/demo_run/<ts>/ with model slug embedded in filenames
# If you want to run the full pytest suite: python tools/one_run.py --tests all
```

Supervisor (ingest → triage → draft sync → sent feedback → learning):
```
python tools/daemon.py
```
