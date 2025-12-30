# Compliance & Data Protection Guide

This document summarises the policies and controls required to operate the cleanroom pipeline in a regulated (e.g. GDPR) environment.

## 1. Data Classification
- **Customer email content:** Personal data. Processed in-memory and forwarded to existing ticketing/CRM systems. Do not store long-term within the pipeline.
- **Knowledge sources:** Generally public/controlled reference data. Treat any account-specific Excel sheets as confidential.
- **Audit metadata:** Scores, matched keys, timestamps. Considered low-sensitivity but still protected as part of operational logs.

## 2. Legal Basis & DPIA
- Ensure the organisation has documented lawful basis (contractual necessity or legitimate interest) for automated preprocessing of customer emails.
- Complete a Data Protection Impact Assessment (DPIA) covering:
  - Purpose of automation and data minimisation steps.
  - Technical controls (local inference, restricted access, logging policies).
  - Incident response processes and contacts.
- Revisit DPIA annually or whenever new data sources/uses are introduced.

## 3. Data Minimisation & Retention
- Disable `PIPELINE_LOG_PATH` if long-term audit history is not allowed. If enabled, rotate/expire logs per retention policy (e.g. 30 days).
- Anonymise or redact exported metrics (e.g. remove raw email bodies before shipping to analytics).
- Purge temporary files (`incoming_emails.replies.xlsx`, scratch exports) after delivery to the ticketing system.

## 4. Access Control
- Grant least-privilege access to `data/`, `docs/`, and runtime hosts. Use group-based permissions backed by directory services (AAD/LDAP).
- Service accounts must use secrets managers or OS keychains; avoid hard-coding credentials.
- Enforce MFA for human operators.

## 5. Encryption & Network
- Run Ollama/pipeline nodes on secure subnets. Restrict inbound firewall rules to expected management ports (SSH, HTTPS) and monitoring.
- Encrypt shared drives (FileVault/APFS encrypted volumes) and ensure backups inherit encryption.
- Use TLS for any API traffic (reverse proxy or run uvicorn behind nginx/traefik).

## 6. Incident Response
1. Contain: stop queue pollers, disable service accounts if compromise suspected.
2. Assess: review `pipeline_history.xlsx` (if retained) and ticketing system to identify affected records.
3. Notify: follow regulatory timelines (GDPR: 72 hours) if a breach is confirmed.
4. Remediate: rotate credentials, rebuild nodes, restore knowledge sources from clean backups.
5. Learn: document post-incident action items and update runbook/compliance docs.

## 7. Data Subject Rights
- Retrieval: use ticketing system as source-of-truth; pipeline should not retain full emails.
- Deletion: ensure any local scratch files or logs relating to the subject are removed once the main system deletes the ticket.
- Document the workflow so operators can respond to requests within statutory deadlines.

## 8. Auditing & Change Management
- Record all production deployments (git SHA, date, operator) in the change log.
- Maintain evidence of control testing (health checks, failover drills, regression runs).
- Review this guide quarterly with Legal/Compliance and update to reflect new regulations or system changes.

Keep this document alongside the DPIA and organisation-wide data protection policies. Update whenever new data sources or jurisdictions are onboarded.
