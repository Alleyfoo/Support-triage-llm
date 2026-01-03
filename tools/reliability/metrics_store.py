from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List


class ReliabilityMetricsStore:
    def __init__(
        self,
        db_path: Path | str = Path("data/reliability.db"),
        history_path: Path | str = Path("reports/reliability/history.jsonl"),
    ) -> None:
        self.db_path = Path(db_path)
        self.history_path = Path(history_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.history_path.parent.mkdir(parents=True, exist_ok=True)

    def ensure(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reliability_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    git_sha TEXT,
                    model_id TEXT,
                    seed INTEGER,
                    n_cases INTEGER,
                    metrics_json TEXT NOT NULL,
                    failures_json_sample TEXT
                )
                """
            )
            conn.commit()

    def insert_run(
        self,
        ts: str,
        git_sha: str | None,
        model_id: str | None,
        seed: int,
        n_cases: int,
        metrics: Dict[str, Any],
        failures_sample: List[Dict[str, Any]],
    ) -> None:
        self.ensure()
        record = {
            "ts": ts,
            "git_sha": git_sha,
            "model_id": model_id,
            "seed": seed,
            "n_cases": n_cases,
            "metrics": metrics,
            "failures_sample": failures_sample,
        }
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO reliability_runs (ts, git_sha, model_id, seed, n_cases, metrics_json, failures_json_sample)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    git_sha,
                    model_id,
                    seed,
                    n_cases,
                    json.dumps(metrics),
                    json.dumps(failures_sample),
                ),
            )
            conn.commit()
        with self.history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def query_recent(self, days: int) -> List[Dict[str, Any]]:
        if not self.db_path.exists():
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        rows: List[Dict[str, Any]] = []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            for row in conn.execute("SELECT ts, git_sha, model_id, seed, n_cases, metrics_json, failures_json_sample FROM reliability_runs"):
                ts = datetime.fromisoformat(row["ts"].replace("Z", "+00:00")).astimezone(timezone.utc)
                if ts < cutoff:
                    continue
                rows.append(
                    {
                        "ts": row["ts"],
                        "git_sha": row["git_sha"],
                        "model_id": row["model_id"],
                        "seed": row["seed"],
                        "n_cases": row["n_cases"],
                        "metrics": json.loads(row["metrics_json"]),
                        "failures": json.loads(row["failures_json_sample"] or "[]"),
                    }
                )
        return rows

    def aggregate_recent(self, days: int) -> Dict[str, Any]:
        rows = self.query_recent(days)
        if not rows:
            return {}
        totals: Dict[str, float] = {}
        counts: Dict[str, int] = {}
        for row in rows:
            metrics = row["metrics"]
            _accumulate_metrics(metrics, totals, counts, prefix="")
        return {k: round(totals[k] / counts[k], 4) for k in totals}


def _accumulate_metrics(metrics: Dict[str, Any], totals: Dict[str, float], counts: Dict[str, int], prefix: str = "") -> None:
    for key, value in metrics.items():
        full_key = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
        if isinstance(value, (int, float)):
            totals[full_key] = totals.get(full_key, 0.0) + float(value)
            counts[full_key] = counts.get(full_key, 0) + 1
        elif isinstance(value, dict):
            _accumulate_metrics(value, totals, counts, prefix=full_key)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Query aggregated reliability metrics.")
    parser.add_argument("--days", type=int, default=7, help="Number of days to include.")
    parser.add_argument("--db", type=Path, default=Path("data/reliability.db"))
    args = parser.parse_args()
    store = ReliabilityMetricsStore(db_path=args.db)
    agg = store.aggregate_recent(args.days)
    print(json.dumps(agg, indent=2))
