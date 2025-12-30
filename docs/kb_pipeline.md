# KB Suggestion Pipeline (placeholder)

Goal: propose KB updates from recurring classifications and escalations without auto-publishing.

Flow:
1) Mine triage + report data (case_type, failure_stage, kb_suggestions, evidence refs).
2) Aggregate recurring patterns (e.g., bounces to a domain, auth_failed for an integration).
3) Output suggestion drafts: title, suggested content, evidence references.

Guardrails:
- No auto-publish; suggestions are drafts for CS/Docs review.
- Cite evidence IDs/timestamps; include case_id for traceability.
- Keep a versioned log of suggestions.

Implementation sketch (future):
- Batch job reads queue db / exports, groups by case_type + top_reasons.
- Writes `data/kb_suggestions.jsonl` with drafts.
- UI section to review/accept/reject suggestions.
