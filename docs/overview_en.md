# Support Triage Copilot — English Overview

A local-first triage and drafting assistant that runs on your machine with Ollama and SQLite/IMAP. It ingests messages, triages with an LLM, suggests tools to gather evidence, drafts replies, and learns from human edits (closed-loop feedback).

Highlights
- Headless email workflow: drafts land in your IMAP Drafts; Sent mail is watched to measure edits and improve.
- Dynamic tools: the LLM proposes tools from the registry; you can add new tools without core code changes.
- Few-shot/RAG: pulls similar past “golden” cases into the prompt to match tone/structure instantly.
- Privacy-first: no external SaaS; uses local Ollama endpoints and local storage.

Run modes
- Docker: `docker compose up -d --build` (fills env from `.env`; mounts `./data` and `./docs`).
- Manual: `python tools/daemon.py` (requires Ollama running).

Health check
- `python tools/status.py` to see last ingest/triage/learn timestamps and queue depth.

Key configs
- `TRIAGE_MODE=llm`, `MODEL_NAME=llama3.1:8b`, `OLLAMA_EMBED_MODEL=nomic-embed-text`
- IMAP: `IMAP_HOST`, `IMAP_USERNAME`, `IMAP_PASSWORD`, `IMAP_FOLDER_DRAFTS`, `IMAP_FOLDER_SENT`
- Knowledge: `KNOWLEDGE_SOURCE=./data/knowledge.md` (your personal key/value context)

Important files
- `tools/daemon.py` — supervisor for ingest/triage/draft sync/sent feedback/learning.
- `tools/status.py` — quick heartbeat.
- `tools/run_learning_cycle.py` — nightly learning batch.
- `docs/specs/FEEDBACK_LOOP.md` — IMAP closed-loop design.
- `docs/specs/DYNAMIC_FEW_SHOT.md` — few-shot/RAG triage design.

Link to this project
- Support-triage-llm: https://github.com/Alleyfoo/Support-triage-llm
