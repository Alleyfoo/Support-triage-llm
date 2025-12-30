# Milestone E — Learning Loop (Dataset + Analytics)

## Scope (what we learn from)
- **E0 (safe now):** synthetic fixtures + scenario suite only. No production data.
- **E1 (approved internal):** redacted case artifacts only, behind policy/approval. Raw text stored separately (or not at all).

## Targets
- Triage quality: case_type, severity, missing questions (redundant-question rate).
- Tool routing accuracy per case_type.
- Report quality: contradictions (draft vs triage), claim-warning count.
- KB suggestions per case_type.

## Outputs
- **E0 metrics (safe):** `data/learning_metrics.json` per demo run:
  - contradiction_rate (draft vs triage fields)
  - redundant_question_rate (asked for info already present)
  - claim_warning_count (report claim checker)
  - routing_accuracy_by_case_type
- **E1 dataset (gated):** redacted triage JSON, evidence bundle summaries (counts/types only), final report JSON, reviewer action (approve/rewrite/escalate). Raw text lives in a restricted store or is omitted.

## Guardrails (must have for E1)
- Explicit approval/policy; banner “do not enable without policy.”
- Redaction before storage and before model calls.
- Data minimisation: store only fields above; no raw evidence payloads unless redacted.
- Retention + deletion (e.g., 30–90 days) and export controls.
- Access control + audit trail; no external uploads unless explicitly allowed.

## Learning path
1) E0: emit `learning_metrics.json` from demo/one_run; tune routing rules and question suppression (“don’t ask time if provided”) from metrics only.
2) E1 (with approval): build gold labels from human actions (approve/rewrite/escalate); adjust routing/templates/questions; consider fine-tuning/RAG only after policy and redaction are enforced.
