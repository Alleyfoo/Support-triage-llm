import json
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import streamlit as st

from tools.reliability.metrics_store import ReliabilityMetricsStore

st.set_page_config(page_title="Reliability Dashboard", layout="wide")
st.title("Reliability Monitoring")
st.caption("Synthetic scenario regression for time extraction and incident localization.")

REPORT_PATH = Path("reports/reliability/latest.json")
store = ReliabilityMetricsStore()


@st.cache_data
def _load_report(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


@st.cache_data
def _load_runs(days: int) -> List[Dict[str, Any]]:
    return store.query_recent(days)


def _flatten_run(run: Dict[str, Any]) -> Dict[str, Any]:
    metrics = run.get("metrics") or {}
    return {
        "ts": run.get("ts"),
        "date_extraction_rate": metrics.get("date_extraction_rate"),
        "pass_rate": metrics.get("pass_rate"),
        "customer_window_iou_avg": metrics.get("customer_window_iou_avg"),
        "query_window_iou_avg": metrics.get("query_window_iou_avg"),
        "incident_window_iou_avg": metrics.get("incident_window_iou_avg"),
        "incident_detection_precision": metrics.get("incident_detection_precision"),
        "incident_detection_recall": metrics.get("incident_detection_recall"),
    }


def _classify_failure(failures: List[str]) -> str:
    failures = failures or []
    if any(f.startswith("schema") for f in failures):
        return "schema"
    if any(f.startswith("incident") for f in failures):
        return "evidence"
    if any(f.startswith("time_") for f in failures):
        return "parse"
    if any("claim" in f for f in failures):
        return "claim"
    return "other"


col1, col2 = st.columns([2, 1])
with col1:
    days = st.select_slider("Window", options=[7, 30, 90], value=30, help="Days of history to display.")
with col2:
    st.write("")
    st.write("Data source: SQLite + history JSONL")

runs = _load_runs(days)
if not runs:
    st.warning("No reliability runs found yet. Run `python -m tools.reliability.validate ...` first.")
    st.stop()

runs_df = pd.DataFrame([_flatten_run(r) for r in runs])
runs_df["ts"] = pd.to_datetime(runs_df["ts"])
runs_df = runs_df.sort_values("ts").set_index("ts")

st.subheader("Trendlines")
chart_cols = st.columns(2)
chart_cols[0].line_chart(runs_df[["date_extraction_rate", "pass_rate"]], height=260, use_container_width=True)
chart_cols[1].line_chart(
    runs_df[
        [
            "customer_window_iou_avg",
            "query_window_iou_avg",
            "incident_window_iou_avg",
            "incident_detection_precision",
            "incident_detection_recall",
        ]
    ],
    height=260,
    use_container_width=True,
)

agg7 = store.aggregate_recent(7)
agg30 = store.aggregate_recent(30)
agg90 = store.aggregate_recent(90)

st.subheader("Aggregates")
agg_col1, agg_col2, agg_col3 = st.columns(3)
agg_col1.metric("Extraction rate (7d)", f"{agg7.get('date_extraction_rate', 0):.2f}")
agg_col1.metric("Customer IoU (7d)", f"{agg7.get('customer_window_iou_avg', 0):.2f}")
agg_col2.metric("Extraction rate (30d)", f"{agg30.get('date_extraction_rate', 0):.2f}")
agg_col2.metric("Customer IoU (30d)", f"{agg30.get('customer_window_iou_avg', 0):.2f}")
agg_col3.metric("Incident IoU (30d)", f"{agg30.get('incident_window_iou_avg', 0):.2f}")
agg_col3.metric("Precision/Recall (30d)", f"{agg30.get('incident_detection_precision', 0):.2f}/{agg30.get('incident_detection_recall', 0):.2f}")

report = _load_report(REPORT_PATH)
failures_raw = report.get("failures", [])
safe_only = st.checkbox("Show only synthetic scenario drill-down", value=True, help="Synthetic scenarios start with scenario_ and do not contain real customer content.")
failures = [f for f in failures_raw if not safe_only or f.get("id", "").startswith("scenario_")]

# Tag accuracy overview (pass/fail by expected_issue)
scenarios_full = report.get("scenarios", [])
tag_rows = []
for sc in scenarios_full:
    truth = sc.get("truth") or {}
    tags = truth.get("tags") or sc.get("tags") or {}
    expected_issue = tags.get("expected_issue") or "none"
    tag_rows.append(
        {
            "expected_issue": expected_issue,
            "status": sc.get("status") or "unknown",
        }
    )
if tag_rows:
    tag_df = pd.DataFrame(tag_rows)
    tag_summary = (
        tag_df.groupby(["expected_issue", "status"]).size().reset_index(name="count")
    )
    st.subheader("Tag Outcome Summary")
    st.dataframe(tag_summary, use_container_width=True, hide_index=True)

if not failures:
    st.info("No failures recorded in the latest run.")
    st.stop()

failure_df = pd.DataFrame(
    [
        {
            "scenario_id": f.get("id"),
            "failure_type": _classify_failure(f.get("failures")),
            "failures": "; ".join(f.get("failures", [])),
            "customer_iou": (f.get("metrics") or {}).get("customer_window_iou"),
            "incident_iou": (f.get("metrics") or {}).get("incident_window_iou"),
            "time_expr": (f.get("truth") or {}).get("tags", {}).get("time_expr") or (f.get("tags") or {}).get("time_expr"),
            "log_pattern": (f.get("truth") or {}).get("tags", {}).get("log_pattern") or (f.get("tags") or {}).get("log_pattern"),
            "expected_issue": (f.get("truth") or {}).get("tags", {}).get("expected_issue") or (f.get("tags") or {}).get("expected_issue"),
        }
        for f in failures
    ]
)

st.subheader("Recent Failures")
st.dataframe(failure_df, use_container_width=True, hide_index=True)

with st.expander("Drill-down"):
    selected_id = st.selectbox("Scenario", failure_df["scenario_id"].tolist())
    selected = next(f for f in failures if f.get("id") == selected_id)
    truth = selected.get("truth") or {}
    triage_payload = selected.get("triage_payload") or {}
    log_result = selected.get("log_result") or {}
    tags = truth.get("tags") or selected.get("tags") or {}

    st.markdown("**Customer text**")
    st.write(truth.get("text", ""))

    st.markdown("**Generation tags**")
    st.json({"time_expr": tags.get("time_expr"), "log_pattern": tags.get("log_pattern"), "has_timezone_hint": tags.get("has_timezone_hint"), "ambiguity_level": tags.get("ambiguity_level")})

    tw_col1, tw_col2 = st.columns(2)
    tw_col1.metric("Customer window", f"{truth.get('customer_time_window')}")
    tw_col1.metric("Expected incident", f"{truth.get('incident_window')}")
    tw_col2.metric("Predicted window", f"{(triage_payload.get('time_window') or {})}")
    tw_col2.metric("Log incident window", f"{(log_result.get('incident_window') or {})}")

    st.markdown("**Evidence snippets**")
    events = log_result.get("events") or []
    if events:
        st.table(pd.DataFrame(events))
    else:
        st.write("No evidence events captured.")

    st.markdown("**Raw payloads (trimmed)**")
    st.json({"triage_payload": triage_payload, "log_result": log_result})
