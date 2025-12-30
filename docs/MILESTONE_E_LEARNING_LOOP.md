# Milestone E: Learning Loop (Dataset + Analytics)

⚠️ **GATED FEATURE — DO NOT ENABLE WITHOUT APPROVAL**
This milestone introduces workflows that can collect and analyze support-case content over time.
In real organizations this may involve privacy, security, legal, and retention requirements.
Default state must remain **OFF**.

See also the root `MILESTONE_E_LEARNING_LOOP.md` for the full plan.

## Purpose

Improve support quality by learning from past cases in a controlled way:

- Improve **triage accuracy** (case_type, severity, missing-info questions)
- Improve **tool selection** (which evidence tools to run per case)
- Improve **final reports** (reduce contradictions, reduce redundant questions)
- Improve **KB suggestions** (more relevant recommendations)
- Produce measurable quality signals (not vibes)

The learning loop is designed to be **additive**: it does not change core rails (allowlisted tools, schemas, claim discipline, no auto-send).

## Modes

### E0 — Metrics-only (safe now)
- No new sensitive storage; compute quality metrics from existing structured artifacts.
- Outputs under `data/learning/metrics/`.

### E1 — Curated dataset (requires approval)
- Redacted triage/report/evidence summaries + reviewer actions.
- Retention + access controls + audit trail.
- Default OFF until explicitly approved and documented.

## Safety gates (for E1)
- Written approval/policy
- Redaction before storage
- Retention and deletion
- Access control + audit trail
- No external upload by default
- Human review remains mandatory
