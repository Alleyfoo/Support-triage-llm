# Chatbot Runbook

This runbook describes how to operate the CS Chatbot LLM demo stack. It replaces the legacy email cleaner procedures and
focuses on the queue-driven ingest ? worker ? dispatcher workflow.

## 1. Components
- **FastAPI webhook (`POST /chat/enqueue`)** – accepts chat messages (text, conversation id, handle) and persists them to the Excel queue via `tools/chat_ingest.py`.
- **Chat worker (`tools/chat_worker.py`)** – claims queued messages, runs `ChatService`, and writes responses back into the workbook.
- **Dispatcher (`tools/chat_dispatcher.py`)** – acknowledges processed rows and logs responses to `data/chat_web_transcript.jsonl` through the web-demo adapter.
- **Streamlit UI (`ui/app.py`)** – optional dashboard for triggering the three stages and reviewing queue/transcript state.

## 2. Prerequisites
- Python 3.11 with dependencies from `requirements.txt`.
- For model-backed answers, an Ollama or llama.cpp runtime configured via environment variables (`MODEL_BACKEND`, `OLLAMA_MODEL`, etc.).
- Writable `data/` directory (Excel queue + transcript) on the host running the demo.

## 3. Startup
1. Activate the virtual environment and install requirements.
2. Launch optional services:
   ```bash
   uvicorn app.server:app --host 0.0.0.0 --port 8000 --reload
   streamlit run ui/app.py
   ```
   The API and Streamlit app can run on the same machine for end-to-end demos.

## 4. Operational Workflow
### Ingest
- Webhook: `POST http://localhost:8000/chat/enqueue` with JSON payload `{ "conversation_id": "web-1", "text": "Hi" }`.
- CLI: `python tools/chat_ingest.py --queue data/email_queue.xlsx "Hi" "Need warranty info"`.
- Streamlit: use the "Enqueue a chat message" form in the sidebar.

### Process
- CLI: `python tools/chat_worker.py --queue data/email_queue.xlsx --processor-id worker-1`.
- Streamlit: click **Run chat worker once**.

### Dispatch
- CLI: `python tools/chat_dispatcher.py --queue data/email_queue.xlsx --dispatcher-id dispatcher-1 --adapter web-demo`.
- Streamlit: click **Dispatch via web demo**.
- Outputs land in `data/chat_web_transcript.jsonl`; load them in Streamlit or `ui/chat_demo.html`.

## 5. Monitoring & Verification
- Queue health: open the Streamlit table or inspect the Excel file manually (queue rows track `status`, `processor_id`, `delivery_status`).
- Performance spot checks: `python tools/benchmark_chat.py --queue data/benchmark_queue.xlsx --reset --repeat 3` prints throughput to confirm worker health.
- Transcript: tail the JSONL file to confirm responses are logged.
- FastAPI health check: `GET /healthz` returns model status.
- Tests: `python -m pytest tests/test_chat_ingest.py tests/test_chat_worker.py tests/test_migrate_queue_chat.py`.

## 6. Guardrails & Escalation
- `ChatService` flags greetings/ambiguous inputs as `clarify`, prompting the user for more detail.
- Mentions of "human/agent" produce a `handoff` decision, keeping `delivery_status = blocked` for manual follow-up.
- Extend `ChatService` and dispatcher logic when adding new channels or escalation policies.

## 7. Housekeeping
- Excel queues/transcripts are demo artifacts; clear them regularly with `rm data/email_queue.xlsx data/chat_web_transcript.jsonl` (or reset via the Streamlit UI).
- Keep `.venv/` and `data/` out of Git (`.gitignore` already handles both).

## 8. Next Steps
- Replace the Excel queue with a transactional store (SQLite/Postgres) for multi-user concurrency.
- Add channel adapters (Slack, Teams) alongside the existing web-demo logger.
- Harden guardrails with richer prompts/tests for multi-turn contexts.
