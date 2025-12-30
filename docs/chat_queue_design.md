# Chatbot Queue Adaptation Plan

## Purpose
Repurpose the existing Excel-backed queue (`data/email_queue.xlsx`) so it can orchestrate inbound and outbound chatbot messages instead of one-shot customer service emails. The goal is to reuse the file path, orchestration scripts, and monitoring surfaces while reshaping the data model and worker behaviours for conversational exchanges.

## Current Queue Snapshot
- **Storage:** single-sheet Excel workbook with immutable column order defined in `tools/process_queue.py`.
- **Intake:** `tools/email_ingest.py` (IMAP or folder watcher) appends cleaned email rows and pre-fills language + `expected_keys` hints.
- **Worker:** `tools/process_queue.py` claims rows with `status in ['queued', '']`, runs `app.pipeline.run_pipeline`, records verification details, and writes the final `reply`.
- **Dispatch:** downstream tooling (`tools/send_drafts_smtp.py`, manual export) sends the cleaned reply via email.
- **Fields:**
  - message metadata (`customer`, `subject`, `language_source`, `ingest_signature`)
  - execution telemetry (`agent`, `started_at`, `latency_seconds`, `score`)
  - content (`body`, `reply`, `answers`, `matched`, `missing`).

Pain points for chat:
- No notion of conversation threads, message direction, or delivery targets.
- Excel queue assumes one reply per email; chats require multi-turn exchanges.
- Dispatch stage only knows how to send SMTP drafts; chat delivery needs per-channel adapters.

## Chat Use Cases
1. **Inbound chat message:** webhook/connector posts a customer message to the queue; workers craft an immediate bot response.
2. **Bot follow-up:** worker may push proactive tips or clarification questions via the same channel.
3. **Human escalation:** worker flags the conversation for agent takeover while preserving transcript context.
4. **System notifications:** analytics jobs append synthetic messages (e.g., SLA alerts) that the bot must route to the right channel.

## Proposed Queue Schema
| Column | Role | Notes |
| --- | --- | --- |
| `message_id` (renamed from `id`) | Unique per inbound/outbound payload. | Use connector-provided ID when available; otherwise UUIDv4. |
| `conversation_id` (new) | Glue for multi-turn context. | Mirrors thread/channel identifiers (`session`, `thread_ts`, `ticket_id`, etc.). |
| `end_user_handle` (renamed from `customer`) | Stable user identifier. | Email, phone, username, or platform ID. |
| `channel` (new) | Delivery surface. | e.g., `web_chat`, `whatsapp`, `slack_support`. Drives routing. |
| `message_direction` (new) | `inbound` / `outbound` / `system`. | Allows dispatcher to filter what still needs delivery. |
| `message_type` (new) | `text`, `rich_card`, `handoff_request`, etc. | Guides rendering + validation. |
| `payload` (renamed from `body`) | Normalised message content. | Plain text for MVP; extend to JSON blobs when needed. |
| `raw_payload` (renamed from `raw_body`) | Original connector payload. | Optional for replay/debug. |
| `language`, `language_source`, `language_confidence` | Keep as-is. | Still valuable for routing multilingual agents. |
| `conversation_tags` (repurposed `expected_keys`) | JSON list of intent or topic tags. | Derived from NLU, knowledge lookups, or connector metadata. |
| `status` | Expanded states. | `queued`, `processing`, `responded`, `awaiting_dispatch`, `delivered`, `handoff`, `failed`. |
| `processor_id` (renamed from `agent`) | Worker identifier. | Tracks which bot instance handled the turn. |
| `started_at`, `finished_at`, `latency_seconds` | Keep, but apply to conversational turns. | Enables SLA metrics per response. |
| `quality_score` (renamed from `score`) | Model quality/guardrail score. | Values outside [0,1] trigger `handoff`. |
| `matched`, `missing` | Keep for compliance and grounding. | Continue storing JSON arrays. |
| `response_payload` (renamed from `reply`) | The bot message to send. | Mirrors `payload` shape (text first). |
| `response_metadata` (renamed from `answers`) | JSON map of tool outputs / citations. | Replaces email-specific answer payload. |
| `delivery_route` (new) | Connector routing hint. | Includes webhook URL, API token reference, queue topic, etc. |
| `delivery_status` (new) | Tracks dispatcher progress. | `pending`, `sent`, `acknowledged`, `errored`. |
| `ingest_signature` | Retain, but base on conversation+message IDs to dedupe. | Prevents duplicates across connectors. |

### Column Migration Strategy
- Update `tools/process_queue.py` to reference the new column names and defaults (introduce an internal mapping for backwards compatibility while tests migrate).
- Extend `load_queue` to coerce missing fields and migrate legacy workbooks on the fly (rename columns when loading).
- Provide a one-time migration script (`tools/migrate_queue_chat.py`) that rewrites headers and seeds new columns for existing demo data.

## Processing Flow
1. **Intake connectors**
   - Replace `email_ingest.py` with `chat_ingest.py` that consumes webhooks, REST polling, or message bus events.
   - FastAPI webhook (`POST /chat/enqueue`) feeds `tools/chat_ingest.py` for web widget simulations.
   - Normalise connector payloads into the schema above; populate `delivery_route` so dispatchers know where to respond.
   - Populate `conversation_tags` via lightweight NLU (keywords, FAQ lookups) to prime the pipeline.
   - For demos, `tools/chat_ingest.py` injects inline messages or JSON payloads into the Excel queue.
2. **Worker (`tools/chat_worker.py`)**
   - Claim the oldest `status == 'queued'` row, mark `processing`, and feed `conversation_id`, `channel`, and recent history into `ChatService.respond`, which wraps the existing pipeline.
   - Augment `run_pipeline` to accept `conversation_context` (list of the last N messages) and produce a `response_payload` plus structured `response_metadata`.
   - When the bot cannot safely answer, set `status = 'handoff'` with `response_payload` containing a human escalation note.
3. **Dispatcher (`chat_dispatcher.py`)**
   - Poll rows with `status == 'responded'` and `delivery_status == 'pending'`.
   - Invoke channel-specific adapters (e.g., `send_to_slack`, `send_to_whatsapp`) using `delivery_route` and `response_payload`.
   - Update `delivery_status` to `sent` or `errored`, and set queue `status` to `delivered` after confirmation.

## Decision States
- `answer`: standard response; queue status -> `responded`, dispatcher sends via configured adapter.
- `clarify`: bot requests more detail; still marked `responded` but transcripts flag the decision for analytics.
- `handoff`: escalation trigger; queue status -> `handoff`, dispatcher leaves `delivery_status` as `blocked` so a human agent can intervene.

### Guardrail Heuristics
- `ChatService` triggers **handoff** when user text references humans/agents; dispatcher marks the row blocked for manual follow-up.
- Short/greeting inputs fall into **clarify** to keep the bot from answering with hallucinations when intent is ambiguous.
- Matched knowledge facts answer directly; otherwise the worker forwards conversation history to `run_pipeline` for grounded responses.
- Extend this section as you harden the LLM prompts (e.g., multi-turn citations, escalation thresholds).

## Conversation State Management
- Store the last N messages for each `conversation_id` in a lightweight cache (SQLite table or JSONL log alongside the queue). For MVP, derive context directly from rows in the Excel file filtered by `conversation_id`.
- Introduce `tools/conversation_cache.py` to encapsulate history queries so we can swap Excel for a database later without touching worker logic.
- Record handoff indicators (e.g., `handoff_reason`, `assigned_agent`) in `response_metadata`.

## Routing & Addressing
- `delivery_route` contains the minimal data the dispatcher needs: connector name, destination identifier, and optional secret reference.
- Derive `delivery_route` during intake; e.g., Slack connector stores `{ "connector": "slack", "channel": "C123", "thread_ts": "169598" }`.
- `chat_dispatcher.py` reads the connector field and calls the appropriate transport layer.

## Transcript Replay
- The dispatcher web-demo adapter logs responses to `data/chat_web_transcript.jsonl`, which `ui/chat_demo.html` can load via the **Load Latest Transcript** control for stakeholder walkthroughs.

## Observability & Audit
- Reuse existing audit workbook (`data/audit.log` or move to SQLite) but log `conversation_id`, `message_id`, and `delivery_status`.
- Extend `ui/monitor.py` to group metrics by `channel` and show conversation backlog rather than email counts.
- Keep `quality_score` thresholds; treat repeated low scores within a conversation as an escalation trigger.
- Use `tools/benchmark_chat.py` to capture turnaround timing for worker processing batches.

## Next Steps
1. Implement schema migration + intake/worker adjustments outlined above.
2. Wire the queue to the new `app/chat_service.py` orchestrator so conversational turns reuse the existing pipeline.
3. Introduce dispatcher service with channel plug-ins and delivery tracking.
4. Update tests (`tests/test_process_queue.py`, `tests/test_email_ingest.py`) to exercise chat scenarios.
5. Replace email-focused docs and runbooks with chat-oriented playbooks once the migration lands.

