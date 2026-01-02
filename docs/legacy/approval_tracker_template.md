## Approval Tracker Template

Use `docs/approval_tracker_template.csv` to capture agent decisions before running `tools/send_approved.py`.

Columns:
- `id`: Queue row ID (must match `data/email_queue.xlsx`)
- `decision`: `approved`, `approve`, `ok` to send; anything else (e.g., `reject`) is ignored by send_approved.
- `comment`: Optional note recorded on the queue row.
- `decided_at`: ISO timestamp (used to avoid resending the same approval)
- `agent`: Optional reviewer identifier

Workflow:
1. Run `python tools/evaluate_queue.py --queue data/email_queue.xlsx --threshold 0.7` after workers finish.
2. Open the approval CSV, add/adjust rows, change `decision` to `approved` for items that should be sent.
3. Execute `python tools/send_approved.py --queue data/email_queue.xlsx --approvals docs/approval_tracker_template.csv`.
4. Sent rows receive `status=sent`, `sent_at`, `sent_agent`, and log entry in `data/approved_sent_log.csv`.
