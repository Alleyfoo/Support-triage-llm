"""Account-specific key data helpers."""

from __future__ import annotations

import hashlib

from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

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

    try:
        df = pd.read_excel(data_path)
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
        rows=int(len(df)),
    )
    records: Dict[str, Dict[str, str]] = {}
    for row in df.to_dict("records"):
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
            if isinstance(value, float) and pd.isna(value):
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

