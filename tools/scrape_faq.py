#!/usr/bin/env python3
"""Fetch FAQ sources and build the Excel knowledge file."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd


@dataclass
class SourceConfig:
    type: str
    location: str
    key_column: Optional[str] = None
    value_column: Optional[str] = None


def _load_config(path: Path) -> Dict[str, object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"Unable to read config {path}: {exc}")


def _resolve_sources(obj: Dict[str, object]) -> List[SourceConfig]:
    sources = obj.get("sources")
    if not isinstance(sources, list):
        raise SystemExit("Config must include a list under 'sources'")
    result: List[SourceConfig] = []
    for entry in sources:
        if not isinstance(entry, dict):
            raise SystemExit("Each source must be an object")
        source_type = entry.get("type")
        location = entry.get("location")
        if not source_type or not location:
            raise SystemExit("Source requires 'type' and 'location'")
        result.append(
            SourceConfig(
                type=str(source_type).lower(),
                location=str(location),
                key_column=entry.get("key_column"),
                value_column=entry.get("value_column"),
            )
        )
    return result


def _from_html_table(cfg: SourceConfig) -> pd.DataFrame:
    try:
        tables = pd.read_html(cfg.location)
    except ValueError:
        return pd.DataFrame(columns=["Key", "Value"])
    key_col = cfg.key_column or "Key"
    value_col = cfg.value_column or "Value"
    for table in tables:
        cols = {str(col).strip().lower(): col for col in table.columns}
        if key_col.lower() in cols and value_col.lower() in cols:
            subset = table[[cols[key_col.lower()], cols[value_col.lower()]]]
            subset.columns = ["Key", "Value"]
            return subset
    raise SystemExit(f"No table with columns '{key_col}'/'{value_col}' found in {cfg.location}")


def _from_csv(cfg: SourceConfig) -> pd.DataFrame:
    df = pd.read_csv(cfg.location)
    key_col = cfg.key_column or "Key"
    value_col = cfg.value_column or "Value"
    if key_col not in df.columns or value_col not in df.columns:
        raise SystemExit(f"CSV {cfg.location} missing columns {key_col}/{value_col}")
    subset = df[[key_col, value_col]].copy()
    subset.columns = ["Key", "Value"]
    return subset


def _from_json(cfg: SourceConfig) -> pd.DataFrame:
    if os.path.isfile(cfg.location):
        raw_text = Path(cfg.location).read_text(encoding="utf-8")
    else:
        from urllib.request import urlopen

        with urlopen(cfg.location, timeout=10) as response:  # nosec - controlled admin config
            raw_text = response.read().decode("utf-8")
    raw = json.loads(raw_text)
    if not isinstance(raw, list):
        raise SystemExit("JSON source must be a list of objects")
    rows = []
    key_col = cfg.key_column or "key"
    value_col = cfg.value_column or "value"
    for item in raw:
        if isinstance(item, dict) and key_col in item and value_col in item:
            rows.append({"Key": item[key_col], "Value": item[value_col]})
    return pd.DataFrame(rows, columns=["Key", "Value"])


def collect_entries(sources: List[SourceConfig]) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    for cfg in sources:
        if cfg.type == "html-table":
            frames.append(_from_html_table(cfg))
        elif cfg.type == "csv":
            frames.append(_from_csv(cfg))
        elif cfg.type == "json":
            frames.append(_from_json(cfg))
        else:
            raise SystemExit(f"Unsupported source type: {cfg.type}")
    if not frames:
        return pd.DataFrame(columns=["Key", "Value"])
    combined = pd.concat(frames, ignore_index=True)
    combined["Key"] = combined["Key"].astype(str).str.strip()
    combined["Value"] = combined["Value"].astype(str).str.strip()
    combined = combined[combined["Key"] != ""]
    return combined.drop_duplicates(subset=["Key"])


def _atomic_write_excel(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w+b", suffix=".xlsx", delete=False, dir=str(path.parent)) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with pd.ExcelWriter(tmp_path, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="knowledge")
        os.replace(tmp_path, path)
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


def _diff_entries(existing: pd.DataFrame, new: pd.DataFrame) -> Dict[str, List[str]]:
    old_map = {str(row["Key"]): str(row["Value"]) for _, row in existing.iterrows()}
    new_map = {str(row["Key"]): str(row["Value"]) for _, row in new.iterrows()}
    added = [key for key in new_map.keys() if key not in old_map]
    removed = [key for key in old_map.keys() if key not in new_map]
    changed = [key for key in new_map.keys() if key in old_map and new_map[key] != old_map[key]]
    return {"added": added, "removed": removed, "changed": changed}


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch FAQ sources into Excel knowledge file")
    parser.add_argument("--config", default="docs/faq_sources.json", help="Path to JSON config (see docs/faq_sources.example.json)")
    parser.add_argument("--output", help="Override output Excel path")
    parser.add_argument("--diff", help="Override diff JSON path")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        raise SystemExit(f"Config not found: {config_path}")
    cfg_obj = _load_config(config_path)
    sources = _resolve_sources(cfg_obj)

    output_path = Path(args.output) if args.output else Path(cfg_obj.get("output", "data/live_faq.xlsx"))
    diff_path = Path(args.diff) if args.diff else Path(cfg_obj.get("diff", "data/live_faq.diff.json"))

    entries = collect_entries(sources)
    if entries.empty:
        raise SystemExit("No FAQ entries collected")

    existing = pd.DataFrame()
    if output_path.exists():
        try:
            existing = pd.read_excel(output_path)
        except Exception:
            existing = pd.DataFrame(columns=["Key", "Value"])

    diff = _diff_entries(existing, entries)
    _atomic_write_excel(output_path, entries)
    diff_path.parent.mkdir(parents=True, exist_ok=True)
    diff_path.write_text(json.dumps(diff, indent=2), encoding="utf-8")

    print(f"Wrote {entries.shape[0]} entries to {output_path}")
    print(f"Diff summary written to {diff_path}")


if __name__ == "__main__":
    main()
