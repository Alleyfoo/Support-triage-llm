# Support Triage Copilot

A local-first, headless triage and drafting bot with closed-loop learning. Runs entirely on your machine (Ollama for LLM + embeddings, SQLite/IMAP for queue and feedback).

Quick links:
- English overview: docs/overview_en.md
- Suomi (FI) overview: docs/overview_fi.md
- Core contract (how it works): docs/contract.md

What it does:
- Ingests email/text into a queue (IMAP or API enqueue).
- Triages with an LLM (or heuristic fallback), proposes tools to gather evidence, and drafts replies.
- Syncs drafts to your IMAP Drafts folder with an Internal Ref footer.
- Watches Sent mail to record human edits and learn (few-shot/RAG over golden dataset).

Start (Docker recommended):
- `cp .env.example .env` and fill IMAP + Ollama settings.
- `docker compose up -d --build`
- Or manual: `python tools/daemon.py` (Ollama must be running).

Health check:
```
python tools/status.py
```
Look for recent “Last Triage” and nonzero “Drafts Waiting”.

Core commands:
- Daemon supervisor: `python tools/daemon.py`
- Force learning cycle: `python tools/run_learning_cycle.py`
- Verify few-shot learning: set `TRIAGE_MODE=llm` + models, then `python tools/verify_learning.py`
- API enqueue example: `curl -X POST http://localhost:8000/triage/enqueue -H "Content-Type: application/json" -H "X-API-KEY: ${INGEST_API_KEY}" -d '{"text":"Emails are bouncing to contoso.com","tenant":"acme"}'`

Env essentials (.env):
- `TRIAGE_MODE=llm`, `MODEL_NAME=llama3.1:8b`, `OLLAMA_HOST=http://ollama:11434`, `OLLAMA_EMBED_MODEL=nomic-embed-text`
- `TOOL_SELECT_MODE=llm`
- IMAP: `IMAP_HOST`, `IMAP_USERNAME`, `IMAP_PASSWORD`, `IMAP_FOLDER_DRAFTS`, `IMAP_FOLDER_SENT`
- `KNOWLEDGE_SOURCE=./data/knowledge.md` (or your own markdown/CSV/XLS key/value table)
- `DB_PATH=/data/queue.db`

Files to know:
- `tools/daemon.py` — scheduler for ingest/triage/draft sync/sent feedback/learning.
- `tools/status.py` — heartbeat/queue depth check.
- `tools/verify_learning.py` — proves few-shot retrieval works.
- `docs/specs/FEEDBACK_LOOP.md` — closed-loop email feedback.
- `docs/specs/DYNAMIC_FEW_SHOT.md` — few-shot/RAG triage prompt.

Notes:
- Keep the Internal Ref footer in drafts/sent mail for closed-loop linking.
- Knowledge loader accepts any key/value content; point `KNOWLEDGE_SOURCE` at your own file.
