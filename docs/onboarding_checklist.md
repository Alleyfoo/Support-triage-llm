# Onboarding Checklist

Use this checklist to bring new operators/engineers onto the cleanroom email pipeline safely.

## 1. Access Requests
- [ ] Create corporate account with MFA enabled.
- [ ] Request read/write access to the shared `data/` directory (knowledge sources, queues, logs).
- [ ] Request execute/maintain permissions on both Mac Minis (or equivalent hosts) running the pipeline.
- [ ] Obtain credentials for the intake mailbox / message bus (IMAP, Graph, or other).
- [ ] Confirm access to monitoring dashboards and alert channels (PagerDuty, Slack, email).

## 2. Local Environment
- [ ] Clone repository: `git clone <repo-url>`.
- [ ] Create virtual environment and install deps: `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`.
- [ ] Verify lint/tests: `python -m pytest` should pass.
- [ ] Pull reference data: copy `data/live_faq.xlsx`, `data/account_records.xlsx`, and `data/incoming_emails.xlsx` from the shared drive.

## 3. Knowledge & Documentation
- [ ] Read `README.md` (focus on Dynamic FAQ, Operational Overview).
- [ ] Review `docs/design_document.md` sections 5, 11, and 12.
- [ ] Study `docs/runbook.md` and rehearse startup/failover steps.
- [ ] Familiarise yourself with `docs/observability.md` and dashboards.
- [ ] Understand the security model (secret key handling, GDPR stance).

## 4. Hands-on Exercises
- [ ] Run the notebook (`notebooks/colab_batch_demo.ipynb`) end to end.
- [ ] Trigger the CLI on sample data: `python -m cli.clean_table data/incoming_emails.xlsx -o tmp.xlsx`.
- [ ] Execute `tools/report_metrics.py` to produce the latest monthly summary.
- [ ] Simulate a secret-key attempt and confirm the security notice behaviour (`tests/test_account_security.py`).
- [ ] Practice promoting standby node in a lab environment.

## 5. Monitoring & Alerts
- [ ] Subscribe to on-call/alert channel.
- [ ] Acknowledge understanding of health probe cadence (5-minute checks).
- [ ] Review escalation path for queue backlog or low quality scores.

## 6. Compliance & Privacy
- [ ] Review GDPR/data-protection requirements with legal/compliance.
- [ ] Confirm log retention policy and how to disable/rotate `PIPELINE_LOG_PATH`.
- [ ] Understand procedure for handling data-subject access requests or deletion requests.

## 7. Sign-off
- [ ] Mentor validates exercises.
- [ ] Manager records onboarding completion date.
- [ ] Onboardee added to runbook/change-log distribution list.

Keep this checklist in your onboarding tracker and update it when the architecture or compliance requirements change.
