# Incident Response Playbook

This guide focuses on the chat-first, SQLite-backed queue. Tailor it to your environment and back up before making changes.

## Queue Backup / Restore
- **Backup:** Snapshot `data/queue.db` regularly to object storage (e.g., S3) with versioning enabled.
- **Restore:** Stop workers, copy the snapshot into `data/queue.db`, restart workers. Validate with `sqlite3 data/queue.db "SELECT COUNT(*) FROM queue;"`.
- **Schema drift:** Run `python -m app.queue_db` (or import `init_db`) to ensure schema indexes exist after restore.

## Ollama / Model Failure
- Symptoms: Worker logs show model/HTTP errors; `/healthz` reports `"ollama": false`.
- Actions: Restart the `ollama` service (`docker compose restart ollama`). If still failing, pull model again or point `OLLAMA_HOST` to a healthy node.
- Queue handling: Workers should catch errors and can set `status='failed'` so items can be retried; clear or re-queue impacted rows once the model is back.

## Poison Pill Messages
- Identify the message by `queue.id` or `message_id` causing repeated crashes.
- Remove or quarantine:
  - Delete from queue: `DELETE FROM queue WHERE id = ?;`
  - Or mark as `handoff`/`failed` and document in audit logs for manual follow-up.
- If context corruption is suspected, also purge related history: `DELETE FROM conversation_history WHERE conversation_id = ?;`.

## Verification After Fix
- Run `/healthz` to confirm DB and Ollama reachability.
- Start 1 worker, process a single message, confirm `status` transitions and history writes.
- Scale workers (or `docker compose up --scale worker=3`) and ensure no duplicate processing.
