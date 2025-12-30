# Observability Guide

This guide describes how to monitor the cleanroom email pipeline, surface key metrics, and wire alerts into your existing observability stack.

## 1. Objectives
- Detect pipeline outages before the queue backs up.
- Track throughput, quality scores, and knowledge drift to inform staffing and policy updates.
- Preserve audit evidence without leaking customer data.

## 2. Signals & Metrics
| Category | Metric | Source | Notes |
| --- | --- | --- | --- |
| Health | `/healthz` status | HTTP probe | Expect `{ "status": "ok", "model_loaded": true }`. Alert after 3 consecutive failures. |
| Queue | Intake backlog | IMAP/Graph or message bus | Count unread messages. Record current backlog and 95th percentile wait time. |
| Throughput | Emails processed per hour/day | `data/pipeline_history.xlsx` | Use `tools/report_metrics.py` or SQL if you ingest history into a warehouse. |
| Quality | Average `evaluation.score` | History file | Track failures (<1.0) by key to spot missing FAQ entries. |
| Errors | Pipeline exceptions | Application logs | Scrub PII before exporting. |
| Knowledge Refresh | Cache hits/misses | Wrap `load_knowledge()` with custom logging if you need to audit live FAQ pulls. |

## 3. Dashboards
1. **Pipeline Overview**
   - Primary/standby node health.
   - Queue depth over time.
   - Emails processed per hour.
   - Rolling average score and failure counts.

2. **Quality Insights**
   - Top `missing` keys (bar chart).
   - Score distribution before/after FAQ updates.
   - SLA compliance (time from arrival to processing).

3. **Resource Utilisation**
   - CPU/memory of Ollama container and API process.
   - Disk usage for `data/` and `models/` volumes.

Suggested tooling:
- Prometheus + Grafana (use exporters or lightweight scripts to push metrics).
- CloudWatch/Stackdriver/Azure Monitor if running in cloud.
- Power BI / Looker for monthly executive summaries fed by the history file.

## 4. Data Collection Hooks
- **History ingestion:** schedule a job that copies `data/pipeline_history.xlsx` into your warehouse (or converts to CSV) nightly. The file contains email text; purge or anonymise if regulations require.
- **Custom metrics exporter:** extend the mailbox poller to emit queue depth, runtime duration, and score metrics to your monitoring backend.
- **Log shipping:** configure Fluent Bit/Vector to tail UVicorn and Ollama logs, redact PII (regex on email addresses, secrets), and forward to your SIEM.

## 5. Alerting
| Condition | Threshold | Response |
| --- | --- | --- |
| Health probe failure | 3 consecutive missed checks | Promote standby, notify on-call. |
| Queue depth exceeds SLA | >30 minutes in queue | Investigate outages, add capacity. |
| Score degradation | >5% of emails <1.0 in last hour | Review missing keys, update knowledge base. |
| Knowledge fallback | Live FAQ unreachable for 3 probes | Alert knowledge owners; pipeline will use template. |
| Storage nearing limits | Disk >80% | Prune logs/history archives. |

## 6. Monthly Reporting Workflow
1. Run the benchmark when you want a consistent latency snapshot:
   ```bash
   python tools/benchmark_pipeline.py --output data/benchmark_report.xlsx
   ```
   The resulting workbook includes `emails`, `results`, and `summary` sheets ready for ingestion.
2. Export metrics for long-term dashboards:
   ```bash
   python tools/report_metrics.py --history data/pipeline_history.xlsx --format json > reports/monthly_metrics.json
   ```
3. Create a companion CSV or PDF summarising:
   - Emails processed.
   - Average score.
   - Total characters/lines handled.
   - Notable incidents or FAQ updates.
4. Review metrics with operations and policy teams; capture action items.

## 7. Testing Observability Changes
- After editing dashboards or probes, run `python -m pytest` to ensure instrumentation did not break core code.
- Use staged rollout (apply changes on standby node first, promote after validation).

## 8. Data Retention & Privacy
- Retain only aggregates once detailed audits are complete. Consider trimming `pipeline_history.xlsx` or storing per-record details in encrypted storage with limited access.
- Document retention periods and ensure deletion jobs run on schedule.

Keep this guide updated whenever new metrics or alerting pathways are introduced.
