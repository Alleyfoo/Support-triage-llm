from pathlib import Path
import pandas as pd
import json
from typing import List, Dict, Any

from .audit import log_file_access, log_function_call


def read_table(path: str) -> pd.DataFrame:
    p = Path(path)
    suffix = p.suffix.lower()
    fmt = suffix.lstrip('.') or 'text'
    log_function_call('read_table', stage='start', path=str(p), format=fmt)

    try:
        if suffix in {".xlsx", ".xls"}:
            frame = pd.read_excel(p)
        else:
            frame = pd.read_csv(p)
    except Exception as exc:
        log_file_access(p, operation='read', status='error', source='io_utils', format=fmt, error=type(exc).__name__)
        raise

    log_file_access(p, operation='read', status='success', source='io_utils', format=fmt, rows=int(len(frame)))
    log_function_call('read_table', stage='completed', path=str(p), format=fmt, rows=int(len(frame)))
    return frame


def write_table(df: pd.DataFrame, path: str) -> None:
    p = Path(path)
    suffix = p.suffix.lower()
    fmt = suffix.lstrip('.') or 'text'
    rows = int(len(df))
    log_function_call('write_table', stage='start', path=str(p), format=fmt, rows=rows)

    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        if suffix in {".xlsx", ".xls"}:
            df.to_excel(p, index=False)
        else:
            df.to_csv(p, index=False)
    except Exception as exc:
        log_file_access(p, operation='write', status='error', source='io_utils', format=fmt, error=type(exc).__name__)
        raise

    log_file_access(p, operation='write', status='success', source='io_utils', format=fmt, rows=rows)
    log_function_call('write_table', stage='completed', path=str(p), format=fmt, rows=rows)


def parse_terms(x: Any) -> List[str]:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return []
    if isinstance(x, list):
        return [str(t).strip() for t in x]
    # "term1; term2; term3"
    return [t.strip() for t in str(x).split(';') if t.strip()]


def serialize(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)

