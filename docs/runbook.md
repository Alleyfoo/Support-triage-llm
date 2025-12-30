> **Migration Note (2025-09-29):** The email-specific runbook below is being replaced by the CS-chatbot workflow.\n> Use the Streamlit playground (`ui/app.py`) and chat queue docs (`docs/chat_queue_design.md`) for the current demo; legacy\n> email procedures remain here until the new runbook is finalized.\n\n# Customer Service Cleaner Runbook

This runbook describes how to operate the cleanroom email pipeline in production, including startup/shutdown, monitoring, failover, and monthly reporting.

## 1. System Overview
- Two Mac Minis (or equivalent hosts) run the pipeline and Ollama container. Only one node drains the email queue at a time; the other remains on standby but healthy.
- Email ingestion uses a shared queue (IMAP folder, Microsoft Graph mailbox, or message bus). Messages remain queued until a worker acknowledges them.
- The pipeline itself is stateless: it loads knowledge sources at runtime, generates replies, then discards intermediate data. Optional audit logs are written to `PIPELINE_LOG_PATH`.

## 2. Prerequisites
- Docker Desktop installed on each Mac Mini (or the host running the containers).
- Git checkout of this repository on both nodes.
- Access to the shared `data/` directory and knowledge sources (Excel/Markdown/HTTP endpoints).
- Service account with permission to read from the intake queue and send replies.
- `.env` (or launch environment) specifying `KNOWLEDGE_SOURCE`, `ACCOUNT_DATA_PATH`, and optional `PIPELINE_LOG_PATH` if audit logs are required.

## 3. Startup Procedure (Primary Node)
1. Pull latest code: `git pull`.
2. (Optional) Update Python deps: `pip install -r requirements.txt`.
3. Start Ollama container:
   ```bash
   docker run --rm -d -p 11434:11434 --name ollama      -v $HOME/.ollama:/root/.ollama ollama/ollama
   docker exec -it ollama ollama pull llama3.1:8b
   ```
4. Export environment variables (or source `.env`).
5. Start the API or batch worker:
   ```bash
   uvicorn app.server:app --host 0.0.0.0 --port 8000
   ```
   or schedule batch runs via `python -m cli.clean_table ...`.
6. Kick off the mailbox poller (cron/systemd job) that drains the queue every 5 minutes.
7. Confirm `/healthz` returns `{"status": "ok", "model_loaded": true}` and that the first batch completes with score `1.0`.

## 4. Standby Node Procedure
- Perform steps 1–5 above but do **not** enable the queue poller.
- Run a health check every 5 minutes to keep the node ready.
- Automate failover via a watchdog (e.g., keepalived, consul, or a lightweight script) that promotes the standby when the primary fails three consecutive health probes.

## 5. Monitoring & Alerts
- **Health probes:** `curl http://<node>:8000/healthz` every 5 minutes; alert if it fails 3 times.
- **Queue depth:** track unread messages in the intake mailbox or the length of the message bus topic.
- **Pipeline KPIs:**
  - emails processed per hour/day/month
  - distribution of `evaluation.score`
  - top missing keys (indicates knowledge drift)
- **System metrics:** container CPU/memory, disk utilisation for `data/` and model cache.

## 6. Failover Drill
1. Simulate failure by stopping the primary Ollama container (`docker stop ollama`) or killing the API process.
2. Verify watchdog promotes standby: it should enable its poller and start draining the queue.
3. Once the primary is fixed, restart it and demote to standby.
4. Document the drill outcome.

## 7. Rolling Updates
1. Drain in-flight work (pause poller and wait until queue depth reaches zero).
2. Deploy changes on the standby node first; run regression tests (`python -m pytest`).
3. Promote standby to primary.
4. Update the former primary and revert to normal roles.

## 8. Knowledge Source Updates
- For `data/live_faq.xlsx` (or any `KNOWLEDGE_SOURCE`): edit the file, ensure correct `Key`/`Value` columns, and save.
- Run `python -m pytest tests/test_dynamic_knowledge.py` to confirm cache refresh behaviour.
- If the source is remote (HTTP share), verify the update endpoint returns the expected data.
- Automated updates: configure `tools/scrape_faq.py --config docs/faq_sources.json` to fetch FAQ pages/tables and rebuild the Excel file atomically. Diff summary is written alongside (default `data/live_faq.diff.json`).
 - Multilingual: generate skeleton files with `python tools/init_multilingual_knowledge.py --out-dir data --langs fi sv en`, then set env vars:
   - `KNOWLEDGE_SOURCE_FI=./data/live_faq_fi.xlsx`
   - `KNOWLEDGE_SOURCE_SV=./data/live_faq_sv.xlsx`
   - `KNOWLEDGE_SOURCE_EN=./data/live_faq_en.xlsx`
   The pipeline auto-selects by `metadata.language`.

## 9. Credential & Secret Rotation
- Replace account Excel sheets (`account_records.xlsx`) and rotate secrets on a regular cadence.
- Update environment variables / secrets manager entries on both nodes.
- Restart workers to pick up new credentials.

## 10. Logs & Troubleshooting
- Check API logs (uvicorn output) for stack traces.
- `data/pipeline_history.xlsx` contains a row per processed email; review low-score rows for missing knowledge keys.
- Ensure `KNOWLEDGE_SOURCE` is reachable; the pipeline falls back to the template only if the primary source fails, so repeated fallbacks indicate a connectivity issue.

## 11. Monthly Reporting & Dashboards
- Use `tools/report_metrics.py` (see below) to aggregate processed emails by month, average score, and email body length.
- Export the generated CSV/JSON into your analytics platform (e.g., Grafana, Power BI).
- Track queued vs processed counts to spot backlog buildup.

## 12. Metric Reporting Script
```
python tools/report_metrics.py --history data/pipeline_history.xlsx --format json --month 2025-09
```
Outputs per-month totals, average score, and total characters/lines processed. Run without `--month` to see all months.

## 13. Incident Response
- Stop pollers to avoid further processing.
- Rotate queue credentials and account record secrets.
- Inspect pipeline history to identify affected emails.
- Restore from git and backups if code or knowledge sources were modified unexpectedly.
- After remediation, rerun regression tests and re-enable processing.

## 14. Data Protection & GDPR Notes
- The pipeline processes data in-memory and does not retain email content beyond optional audit logs.
- If logs are required, either anonymise or store on encrypted volumes with retention policies.
- Document the data-processing agreement covering the mailbox/queue and knowledge sources.
- Provide operators with clear deletion procedures if a right-to-be-forgotten request arrives.

## 15. Checklist Summary
- [ ] Primary node online, poller active, `/healthz` healthy.
- [ ] Standby node online, poller paused, health checks passing.
- [ ] Queue monitored for backlog and SLA.
- [ ] Knowledge source reachable and refresh tests green.
- [ ] Monthly metrics captured and reviewed.
- [ ] Credentials rotated per policy and secrets stored securely.
- [ ] Runbook reviewed quarterly and updated after each incident.

## 16. Queue Operations
- Initialise the queue from the latest dataset:
  ```bash
  python tools/process_queue.py --init-from data/test_emails.json --queue data/email_queue.xlsx --overwrite
  ```
- Start a worker on each node (use distinct `--agent-name` values):
  ```bash
  python tools/process_queue.py --queue data/email_queue.xlsx --agent-name agent-primary --watch --poll-interval 5
  ```
- Each worker picks the first row with `status` queued, marks it processing, records timestamps/latency/score, and completes the row. Multiple workers can share the same Excel file because they immediately write the in-progress status before generating replies. If the pipeline cannot find a relevant knowledge entry, the worker sets `status = human-review` so agents can follow up manually.
- If the queue is empty, workers sleep for the poll interval and retry; stop them with Ctrl+C to pause processing.
- Archive or reset the queue workbook after a batch by copying `data/email_queue.xlsx` to an audit location and reinitialising if needed.

## 17. Demo Jobs & Scripts (End-to-End Flow)

This section lists the demo-friendly jobs that implement “email → queue → worker → reply,” plus benchmarking. Use these as building blocks; schedule them with Task Scheduler/cron in real deployments.

- Generate demo emails (.eml files):
  - `python tools/email_generator.py --out-dir notebooks/data/inbox --count 20`
  - Creates `.eml` test messages and `email_index.csv` for reference.
- Ingest emails into the Excel-backed queue:
  - Folder mode: `python tools/email_ingest.py --folder notebooks/data/inbox --queue data/email_queue.xlsx --watch --poll-interval 10`
  - IMAP mode: `python tools/email_ingest.py --imap --queue data/email_queue.xlsx --watch --poll-interval 15`
  - Flags: `--no-clean` (skip normalisation), `--retain-raw` (store original body in `raw_body`), `--no-detect` (leave `expected_keys` empty).
  - Deduplication: the ingestor skips emails whose subject+body hash already exists. Use `--archive-folder processed_eml` to move handled `.eml` files, or `--delete-processed` to remove them after ingestion.
  - Each row includes `language`, `language_source`, and `language_confidence` combining domain suffix hints with automatic detection (Finnish, Swedish, English initially).
    - Requires `IMAP_HOST`, `IMAP_USERNAME`, `IMAP_PASSWORD` in the environment; optional: `IMAP_FOLDER`, `IMAP_SSL`, `IMAP_PORT`.
- Process the queue:
  - `python tools/process_queue.py --queue data/email_queue.xlsx --agent-name agent-1 --watch`
  - Ensure the backend is set for real model calls: `$env:MODEL_BACKEND="ollama"; $env:OLLAMA_MODEL="llama3.1:8b"; $env:OLLAMA_HOST="http://127.0.0.1:11434"`.
  - Monitor the system:
    - `streamlit run ui/monitor.py` (auto-refresh available in the sidebar).
  - Benchmark the pipeline (synthetic dataset):
    - `python tools/benchmark_pipeline.py --dataset data/test_emails.json --count 100 --warmup 1 --include-prompts --output data/benchmark_report.xlsx --log-csv data/benchmark_log.csv`
  - Benchmark Ollama directly (bypassing pipeline):
    - `python tools/ollama_direct_benchmark.py --prompt "Ping" --model llama3.1:8b --count 20 --warmup 1 --num-predict 64 --temperature 0.2 --stream --include-prompts --output data/ollama_direct_benchmark.xlsx --log-csv data/ollama_direct_benchmark_log.csv`
    - Send drafts to CS mailbox via SMTP (demo):
      - `python tools/send_drafts_smtp.py --queue data/email_queue.xlsx`
      - Env: `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_STARTTLS` (1), `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_TO`
  - Evaluate reply quality & flag low scores:
    - `python tools/evaluate_queue.py --queue data/email_queue.xlsx --threshold 0.7 --agent-name qa-1`
  - Send approved replies to customers:
    - `python tools/send_approved.py --queue data/email_queue.xlsx --approvals data/approvals.csv`
    - Approvals CSV columns: `id, decision, comment, decided_at` (decision = approved/approve). Uses the same SMTP env vars as drafts.

## 18. Scheduling (Windows/macOS/Linux)

Suggested tasks and cadence:
- Ingestion (IMAP or folder): every 1–5 minutes.
- Worker: long-running (restart on failure); one per agent name.
- Benchmarks: hourly direct Ollama benchmark to CSV for trend charts.
- FAQ refresh: daily `python tools/scrape_faq.py --config docs/faq_sources.json` (only if automated scraping is allowed).

Operators should run a preflight before starting workers:
- `python tools/preflight_check.py --all`

Windows Task Scheduler (outline):
- Create a task that runs: `python <repo>\tools\email_ingest.py --imap --queue <repo>\data\email_queue.xlsx --watch --poll-interval 60`
- Create a task that runs at logon/startup: `python <repo>\tools\process_queue.py --queue <repo>\data\email_queue.xlsx --agent-name agent-1 --watch`

Linux/macOS (cron/systemd):
- Cron example: `*/5 * * * * /usr/bin/python /srv/cleanroom/tools/email_ingest.py --imap --queue /srv/cleanroom/data/email_queue.xlsx --watch --poll-interval 300`
- Systemd units for long-running worker and dashboard.

Docker Compose (future):
- Define services for `ollama`, `worker`, `dashboard`, optional `ingest`.
- Use healthchecks and restart policies; mount `data/` volume for artifacts.

Design notes:
- The knowledge base is dynamic (Excel/CSV/Markdown/HTTP). Update `KNOWLEDGE_SOURCE` to point at your live FAQ.
- The pipeline appends each run to `data/pipeline_history.xlsx` with atomic file writes, reducing corruption risk.
- The queue workbook is suitable for demos; for concurrency/scale, plan to replace it with a broker/DB with atomic updates.


