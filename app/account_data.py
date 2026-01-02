"""Account-specific key data helpers."""

from __future__ import annotations

import hashlib

from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

import json

from .audit import log_file_access, log_function_call
from .config import ACCOUNT_DATA_PATH


@lru_cache(maxsize=1)
def load_account_records(path: Optional[str] = None) -> Dict[str, Dict[str, str]]:
    """Return account records keyed by normalised email."""

    data_path = Path(path or ACCOUNT_DATA_PATH)
    path_str = str(data_path)
    log_function_call('load_account_records', stage='start', path=path_str)
    if not data_path.exists():
        log_file_access(data_path, operation='read', status='missing', source='account_records')
        log_function_call('load_account_records', stage='completed', path=path_str, records=0)
        return {}

    records: Dict[str, Dict[str, str]] = {}
    suffix = data_path.suffix.lower()
    try:
        if suffix in {".json"}:
            raw = json.loads(data_path.read_text(encoding="utf-8"))
            rows = raw if isinstance(raw, list) else []
        elif suffix in {".csv", ".tsv"}:
            import pandas as pd  # type: ignore

            df = pd.read_csv(data_path)
            rows = df.to_dict("records")
        elif suffix in {".xlsx", ".xls"}:
            try:
                import pandas as pd  # type: ignore
            except Exception as exc:
                raise ImportError("pandas/openpyxl required to read Excel account records") from exc
            df = pd.read_excel(data_path)
            rows = df.to_dict("records")
        else:
            raise ValueError(f"Unsupported account data format: {data_path.suffix}")
    except Exception as exc:
        log_file_access(
            data_path,
            operation='read',
            status='error',
            source='account_records',
            error=type(exc).__name__,
        )
        raise

    log_file_access(
        data_path,
        operation='read',
        status='success',
        source='account_records',
        rows=len(rows),
    )
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_email = row.get("email")
        if raw_email is None:
            continue
        email = str(raw_email).strip().lower()
        if not email:
            continue

        clean_row: Dict[str, str] = {}
        for key, value in row.items():
            if key == "email":
                continue
            if value is None:
                continue
            clean_row[key] = str(value).strip()

        records[email] = clean_row

    log_function_call('load_account_records', stage='completed', path=path_str, records=len(records))
    return records


def get_account_record(email: Optional[str], path: Optional[str] = None) -> Dict[str, str]:
    """Fetch a single account record by email (case-insensitive)."""

    if not email:
        log_function_call('get_account_record', stage='skipped', reason='empty_email')
        return {}
    normalised = str(email).strip().lower()
    if not normalised:
        log_function_call('get_account_record', stage='skipped', reason='blank_email')
        return {}

    email_hash = hashlib.sha256(normalised.encode('utf-8')).hexdigest()[:12]
    log_function_call('get_account_record', stage='request', email_hash=email_hash)
    record = load_account_records(path).get(normalised, {}).copy()
    log_function_call('get_account_record', stage='completed', email_hash=email_hash, found=bool(record))
    return record

