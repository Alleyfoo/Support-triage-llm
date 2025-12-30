#!/usr/bin/env python3
"""Create skeleton multilingual knowledge workbooks (Key/Value) per language.

This helps bootstrap Finnish/Swedish/English knowledge files that the pipeline
can select based on `metadata.language` and the env vars:
  KNOWLEDGE_SOURCE_FI, KNOWLEDGE_SOURCE_SV, KNOWLEDGE_SOURCE_EN
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, List

import pandas as pd

DEFAULT_KEYS: List[str] = [
    "company_name",
    "founded_year",
    "headquarters",
    "support_hours",
    "support_email",
    "warranty_policy",
    "return_policy",
    "shipping_time",
    "loyalty_program",
    "premium_support",
    "account_security_notice",
]


def write_workbook(path: Path, keys: List[str], seed: Dict[str, str] | None = None) -> None:
    seed = seed or {}
    rows = [{"Key": k, "Value": seed.get(k, "")} for k in keys]
    df = pd.DataFrame(rows, columns=["Key", "Value"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="knowledge")


def main() -> None:
    ap = argparse.ArgumentParser(description="Init multilingual knowledge files")
    ap.add_argument("--out-dir", default="data", help="Directory to write files into")
    ap.add_argument("--langs", nargs="*", default=["fi", "sv", "en"], help="Languages to create (codes)")
    ap.add_argument("--keys", nargs="*", help="Override key list; defaults to a standard set")
    args = ap.parse_args()

    keys = args.keys or DEFAULT_KEYS
    out = Path(args.out_dir)
    for lang in args.langs:
        path = out / f"live_faq_{lang}.xlsx"
        write_workbook(path, keys)
        print(f"Wrote {path}")


if __name__ == "__main__":
    main()

