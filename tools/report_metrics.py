#!/usr/bin/env python3
"""Monthly metrics summariser for pipeline history."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Optional

import pandas as pd


def _load_history(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        raise SystemExit(f"History file missing or empty: {path}")
    suffix = path.suffix.lower()
    try:
        if suffix in {".xlsx", ".xls"}:
            return pd.read_excel(path)
        return pd.read_csv(path)
    except Exception as exc:
        raise SystemExit(f"Unable to read history file {path}: {exc}") from exc


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    required = {"email", "reply", "score"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"History file missing required columns: {sorted(missing)}")

    df = df.copy()

    if "processed_at" in df.columns:
        df["processed_at"] = pd.to_datetime(df["processed_at"], errors="coerce")
    else:
        df["processed_at"] = pd.Timestamp.utcnow()
    if df["processed_at"].isna().any():
        df.loc[df["processed_at"].isna(), "processed_at"] = pd.Timestamp.utcnow()

    df["score"] = pd.to_numeric(df["score"], errors="coerce").fillna(0.0)

    emails = df["email"].fillna("")
    df["email_lines"] = emails.apply(lambda x: str(x).count("\n") + 1)
    df["email_chars"] = emails.apply(lambda x: len(str(x)))

    return df


def summarise(df: pd.DataFrame, month: Optional[str]) -> Dict[str, Dict[str, float]]:
    df = _normalise(df)
    df["month"] = df["processed_at"].dt.to_period("M").astype(str)

    if month:
        df = df[df["month"] == month]
        if df.empty:
            raise SystemExit(f"No records found for month {month}")

    grouped = df.groupby("month")
    summary: Dict[str, Dict[str, float]] = {}
    for key, group in grouped:
        summary[key] = {
            "emails": int(group.shape[0]),
            "avg_score": round(group["score"].mean(), 3),
            "total_email_lines": int(group["email_lines"].sum()),
            "total_email_chars": int(group["email_chars"].sum()),
        }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarise pipeline history metrics")
    parser.add_argument("--history", default="data/pipeline_history.xlsx", help="Path to history CSV/XLSX")
    parser.add_argument("--month", help="Filter to YYYY-MM")
    parser.add_argument("--format", choices={"table", "json"}, default="table")
    args = parser.parse_args()

    df = _load_history(Path(args.history))
    summary = summarise(df, args.month)

    if args.format == "json":
        print(json.dumps(summary, indent=2))
    else:
        for month, metrics in summary.items():
            print()
            print(f"Month: {month}")
            for key, value in metrics.items():
                print(f"  {key:>18}: {value}")



if __name__ == "__main__":
    main()
