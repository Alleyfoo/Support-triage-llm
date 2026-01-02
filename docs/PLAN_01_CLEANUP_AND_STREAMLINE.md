# Support Triage Copilot — Cleanup & Streamline Plan (Agent Instructions)

Goal: make the repo one coherent product by archiving obsolete code, tightening deps, and consolidating docs so daily ops run without archaeology.

Decision: Path A (recommended) — Triage Copilot as the core product. The legacy “pipeline history” subsystem is kept only as an optional extension (enable with `FEATURE_PIPELINE=1`); daily ops/CI run without it.

Checklist:
- Define live surface: add `docs/LIVE_SURFACE.md` with supported entrypoints (API, daemon/worker, Streamlit UI), required data/env, and “not supported” (anything in `legacy/`).
- Orphan audit: add `tools/audit_imports.py` that starts from live entrypoints, walks imports, and writes `reports/orphan_modules.txt` + `reports/orphan_files.txt` for modules never imported.
- Archive/remove: move historical-but-useful files into `legacy/<topic>/` with a short README; delete junk. Update docs index to avoid dangling references.
- Dependency cleanup: choose XLSX stance. Either keep XLSX (move `openpyxl` to runtime `requirements.txt` and keep `pd.read_excel` paths) or drop XLSX (migrate to CSV/JSON and remove `openpyxl`). Be consistent with `app/account_data.py` and any Excel helpers.
- Docs spine: `README.md` is entry point (what/quickstart/daily workflow/troubleshooting). `docs/INDEX.md` is the map. `docs/specs/*` hold deep specs. `docs/legacy/*` is historical only.
- Repo hygiene: ensure `pytest.ini` excludes `legacy/`. Add `Makefile`/`justfile` targets: `test`, `run-daemon`, `run-ui`, `lint` (optional).

Definition of done:
- Single live product (SQLite triage). No live tests import legacy/pipeline code.
- README “run it now” works.
- Dependencies minimal and accurate.
- A new dev can answer “what runs daily?” in under 2 minutes.
