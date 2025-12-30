import os
from pathlib import Path

import pandas as pd
import streamlit as st
try:
    from streamlit_autorefresh import st_autorefresh  # type: ignore
except Exception:  # pragma: no cover - optional
    st_autorefresh = None  # type: ignore


st.set_page_config(page_title="Cleanroom Monitoring", layout="wide")
st.title("Cleanroom Monitoring Dashboard")
st.caption("Live view of queue, pipeline history, and benchmarks")


def load_df(path: Path, *, kind: str) -> pd.DataFrame | None:
    try:
        if not path.exists():
            return None
        if kind == "excel":
            return pd.read_excel(path)
        return pd.read_csv(path)
    except Exception as exc:
        st.warning(f"Unable to read {path}: {exc}")
        return None


with st.sidebar:
    st.header("Controls")
    refresh_sec = st.slider("Auto-refresh (seconds)", min_value=0, max_value=60, value=5)
    if refresh_sec and st_autorefresh:
        st_autorefresh(interval=refresh_sec * 1000, key="auto_refresh")
    elif refresh_sec:
        # Fallback meta-refresh if the helper package isn't installed
        st.write(f'<meta http-equiv="refresh" content="{refresh_sec}">', unsafe_allow_html=True)
    if st.button("Refresh now"):
        # Streamlit renamed experimental_rerun() to rerun(); support both.
        _rerun = getattr(st, "rerun", None)
        if callable(_rerun):
            _rerun()
        else:
            _exp_rerun = getattr(st, "experimental_rerun", None)
            if callable(_exp_rerun):
                _exp_rerun()


colA, colB = st.columns(2)

# Queue section
with colA:
    st.subheader("Queue status (data/email_queue.xlsx)")
    queue_df = load_df(Path("data/email_queue.xlsx"), kind="excel")
    if queue_df is None or queue_df.empty:
        st.info("No queue file found or empty.")
    else:
        status_series = queue_df["status"].astype(str).str.lower()
        total = queue_df.shape[0]
        queued = int((status_series == "queued").sum())
        processing = int((status_series == "processing").sum())
        done = int((status_series == "done").sum())
        human_review = int((status_series == "human-review").sum())
        col_metrics1, col_metrics2, col_metrics3, col_metrics4 = st.columns(4)
        col_metrics1.metric("Queued", queued)
        col_metrics2.metric("Processing", processing)
        col_metrics3.metric("Done", done)
        col_metrics4.metric("Human review", human_review)
        st.caption(f"Total rows: {total}")

        status_counts = (
            queue_df.assign(status=status_series)
            .groupby("status")["id"].count()
            .rename("count")
            .reset_index()
        )
        st.dataframe(status_counts)
        # Language breakdown
        st.caption("By language")
        lang_col = queue_df.get("language")
        if lang_col is not None:
            lang_stats = (
                queue_df.assign(
                    _lang=queue_df["language"].astype(str).str.lower().replace({"nan": ""}),
                    _status=status_series,
                )
            )
            lang_counts = lang_stats.groupby("_lang")["id"].count().rename("total").reset_index()
            hr = (
                lang_stats.assign(_hr=lang_stats["_status"].eq("human-review").astype(int))
                .groupby("_lang")["_hr"].sum()
                .rename("human_review")
            )
            lang_table = lang_counts.merge(hr, left_on="_lang", right_index=True, how="left").fillna(0)
            st.dataframe(lang_table)
        if "latency_seconds" in queue_df.columns:
            by_agent = (
                queue_df.dropna(subset=["latency_seconds"])\
                .groupby("agent")["latency_seconds"].agg(["count", "mean", "median", "max"]).reset_index()
            )
            st.caption("Latency by agent")
            st.dataframe(by_agent)
        if "quality_score" in queue_df.columns:
            st.caption("Quality scores")
            qs = pd.to_numeric(queue_df["quality_score"], errors="coerce").dropna()
            if not qs.empty:
                st.bar_chart(qs)
        st.caption("Most recent 10 processed")
        recent = queue_df.sort_values(by="finished_at", ascending=False).head(10)
        st.dataframe(
            recent[[c for c in ["id", "agent", "status", "latency_seconds", "score", "finished_at", "subject"] if c in recent.columns]]
        )

# Pipeline history
with colB:
    st.subheader("Pipeline history (data/pipeline_history.xlsx)")
    hist_df = load_df(Path("data/pipeline_history.xlsx"), kind="excel")
    if hist_df is None or hist_df.empty:
        st.info("No pipeline history yet.")
    else:
        # Basic score distribution
        if "score" in hist_df.columns:
            st.caption("Score distribution")
            st.bar_chart(hist_df["score"].fillna(0.0))
        st.caption("Last 10 entries")
        st.dataframe(hist_df.tail(10))

st.markdown("---")
st.subheader("Benchmarks")
bench_col1, bench_col2 = st.columns(2)

with bench_col1:
    st.caption("Pipeline benchmark log (data/benchmark_log.csv)")
    bench_df = load_df(Path("data/benchmark_log.csv"), kind="csv")
    if bench_df is None or bench_df.empty:
        st.info("No pipeline benchmark log.")
    else:
        st.dataframe(bench_df[[c for c in ["id", "subject", "elapsed_seconds", "score", "human_review"] if c in bench_df.columns]].head(25))
        st.caption("Latency (seconds)")
        st.line_chart(bench_df["elapsed_seconds"].astype(float))

with bench_col2:
    st.caption("Direct Ollama benchmark (data/ollama_direct_benchmark_log.csv)")
    direct_df = load_df(Path("data/ollama_direct_benchmark_log.csv"), kind="csv")
    if direct_df is None or direct_df.empty:
        st.info("No direct benchmark log.")
    else:
        st.dataframe(direct_df[[c for c in ["iteration", "elapsed_seconds", "ok"] if c in direct_df.columns]].head(25))
        st.caption("Latency (seconds)")
        st.line_chart(direct_df["elapsed_seconds"].astype(float))

st.markdown("---")
st.caption("Tip: run tools/ollama_direct_benchmark.py and tools/benchmark_pipeline.py to populate logs.")
