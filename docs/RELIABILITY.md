Reliability harness for time-window extraction and log localization.

## Components
- Generator: `python -m tools.reliability.generate --out tests/fixtures/generated --seed 123 --n 200`. Produces `scenarios/*.json` and matching `logs/*.jsonl` with ground truth (customer/query/incident windows, case_type, severity).
- Validator: `python -m tools.reliability.validate --scenarios tests/fixtures/generated/scenarios --logs tests/fixtures/generated/logs --out reports/reliability/latest.json`. Writes JSON + Markdown, records a history row in SQLite + `reports/reliability/history.jsonl`.
- Metrics store: SQLite at `data/reliability.db` table `reliability_runs` (ts, git_sha, model_id, seed, n_cases, metrics_json, failures_json_sample). Query aggregates: `python -m tools.reliability.metrics_store --days 30`.
- Dashboard: `streamlit run ui/reliability.py` (default safe mode shows aggregated metrics; drill-down limited to synthetic scenarios unless explicitly unchecked).

## Scoring
- IoU for ranges: `overlap / union` of predicted vs expected start/end (UTC). Used for customer, query, and incident windows.
- Exact match: start/end string equality (regression guard).
- Detection: precision/recall from observed_incident flags.
- Claim safety: fail if incident is absent and the evidence summary lacks the guardrail (“absence of evidence is not proof of absence”).
- Schema/tool failures: counted into rates for quick sanity checks.

## Outputs
- `reports/reliability/latest.json`: run metadata, per-scenario results, failures sample.
- `reports/reliability/latest.md`: human summary with key metrics + recent failures.
- `reports/reliability/history.jsonl`: append-only ledger of runs.

## Golden set
- Seeded run (`--seed 123 --n 50`) is checked in under `tests/fixtures/generated` for reproducible gating.
- Regenerate larger sets locally with the same CLI; outputs are deterministic per seed.

## Drill-down (safe by default)
- Dashboard failure table filters to `scenario_*` IDs unless “Show only synthetic scenario drill-down” is unchecked.
- Drill-down shows: input text, expected vs predicted windows, and sampled evidence events.
