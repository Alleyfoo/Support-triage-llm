# CS Chatbot LLM

Local-first chatbot playground now running on an SQLite-backed queue (no shared Excel file) for safe multi-worker processing. The goal is to keep things portable while making the queue/worker path production-ready.

## Overview
- SQLite queue + history: `app/queue_db.py` manages the `queue` and `conversation_history` tables; `USE_DB_QUEUE` defaults to `true`.
- Multi-worker safe: `tools/chat_worker.py` claims rows transactionally to avoid Excel-style overwrites.
- ChatService: wraps the legacy pipeline/knowledge stack for multi-turn answers; worker writes queue status and conversation history.
- API key on ingest: `/chat/enqueue` requires `X-API-KEY` when `REQUIRE_API_KEY=true` (default in Docker Compose).
- Dockerized: `docker-compose.yml` brings up FastAPI + worker + Ollama on an internal network; SQLite lives in a shared volume.
- Legacy Excel path: still available when `USE_DB_QUEUE=false`, but defaults to DB for concurrency safety.

## Webhook API
Start the FastAPI server (DB path by default):
```bash
export INGEST_API_KEY=dev-api-key   # set your own secret in real deployments
uvicorn app.server:app --reload --host 0.0.0.0 --port 8000
```
Enqueue chat messages with API key auth:
```bash
curl -X POST http://localhost:8000/chat/enqueue \
     -H "Content-Type: application/json" \
     -H "X-API-KEY: ${INGEST_API_KEY}" \
     -d '{"conversation_id": "web-visit-1", "text": "Need warranty info", "end_user_handle": "visitor-1"}'
```
Run the worker (SQLite path):
```bash
USE_DB_QUEUE=true python tools/chat_worker.py --processor-id cli-worker --watch
```

## Quickstart
### 1. Environment setup
```bash
python -m venv .venv
. .venv/Scripts/activate        # PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# set an ingest API key for the server
setx INGEST_API_KEY dev-api-key
```

### 2. Run with Docker Compose (preferred for demos)
```bash
docker compose up --build
# Scale workers if desired:
docker compose up --build --scale worker=3
```

### 3. Local CLI workflow (DB-backed)
```bash
# Enqueue
USE_DB_QUEUE=true python tools/chat_ingest.py --conversation-id demo-web "Hello" "When were you founded?"

# Run a worker loop
USE_DB_QUEUE=true python tools/chat_worker.py --processor-id cli-worker --watch
```
Use Streamlit (`ui/app.py`) or the static demo (`ui/chat_demo.html`) if you want a quick UI; set `USE_DB_QUEUE=false` if you need the old Excel-backed demo.

### 4. Load testing
Requires `locust` (install separately): `pip install locust`
```bash
INGEST_API_KEY=dev-api-key locust -f load_tests/locustfile.py --host http://localhost:8000
```

## File map
- `app/chat_service.py` - conversational wrapper around the original pipeline/knowledge stack.
- `app/queue_db.py` - SQLite schema/helpers for the queue and conversation history.
- `tools/chat_ingest.py` - enqueue inline strings or JSON payloads into the DB (or Excel when opted).
- `tools/chat_worker.py` - claims queued rows transactionally, calls `ChatService`, writes reply + history.
- `tools/chat_dispatcher.py` - acknowledges `responded` rows and logs them to a transcript for demos.
- `ui/app.py` - Streamlit dashboard for enqueue → worker → dispatch.
- `ui/chat_demo.html` - static HTML mock with quick answers + transcript loader.

## Knowledge & data sources
Grounding facts still come from the markdown/Excel knowledge templates described in `docs/chat_queue_design.md`. During development the repo keeps sample data only as locally generated artifacts; `.gitignore` blocks anything under `data/` except for the `.gitkeep` sentinel.
