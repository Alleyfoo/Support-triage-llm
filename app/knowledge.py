"""Utilities for loading structured customer service knowledge."""

from __future__ import annotations

import io
import time
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import pandas as pd

from . import config
from .audit import log_file_access, log_function_call


_KNOWLEDGE_CACHE: Dict[str, Optional[object]] = {
    "data": None,
    "source": None,
    "timestamp": 0.0,
    "file_mtime": None,
}


def _is_url(source: str) -> bool:
    return source.startswith(("http://", "https://"))


def _knowledge_from_markdown(raw_text: str) -> Dict[str, str]:
    knowledge: Dict[str, str] = {}
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 2:
            continue
        key, value = cells[0], cells[1]
        if key.lower() == "key" or not key:
            continue
        knowledge[key] = value
    return knowledge


def _knowledge_from_dataframe(df: pd.DataFrame) -> Dict[str, str]:
    columns = {str(col).strip().lower(): col for col in df.columns}
    key_column = columns.get("key")
    value_column = columns.get("value")
    if key_column is None or value_column is None:
        raise ValueError("Knowledge table must include 'Key' and 'Value' columns.")

    knowledge: Dict[str, str] = {}
    subset = df[[key_column, value_column]].to_dict('records')
    for row in subset:
        key = row.get(key_column)
        value = row.get(value_column)
        key_str = '' if key is None else str(key).strip()
        if not key_str or key_str.lower() == 'key':
            continue
        if isinstance(value, float) and pd.isna(value):
            continue
        value_str = '' if value is None else str(value).strip()
        knowledge[key_str] = value_str
    return knowledge


def _load_from_local(path: Path) -> Tuple[Dict[str, str], Optional[float]]:
    if not path.exists():
        log_file_access(path, operation='read', status='missing', source='knowledge_local')
        raise FileNotFoundError(f"Knowledge source not found at {path}")

    suffix = path.suffix.lower()
    fmt = suffix.lstrip('.') or 'text'
    try:
        if suffix in {".xlsx", ".xls"}:
            df = pd.read_excel(path)
            knowledge = _knowledge_from_dataframe(df)
        elif suffix in {".csv", ".tsv"}:
            df = pd.read_csv(path)
            knowledge = _knowledge_from_dataframe(df)
        else:
            raw_text = path.read_text(encoding='utf-8')
            knowledge = _knowledge_from_markdown(raw_text)
    except Exception as exc:
        log_file_access(
            path,
            operation='read',
            status='error',
            source='knowledge_local',
            format=fmt,
            error=type(exc).__name__,
        )
        raise

    log_file_access(
        path,
        operation='read',
        status='success',
        source='knowledge_local',
        format=fmt,
        entries=len(knowledge),
    )
    mtime = path.stat().st_mtime
    return knowledge, mtime




def _load_from_url(source: str) -> Tuple[Dict[str, str], Optional[float]]:
    request = Request(source, headers={"User-Agent": "cs-slm-cleaner/1.0"})
    try:
        with urlopen(request, timeout=15) as response:  # nosec - trusted admin-configured endpoints
            data = response.read()
            encoding = response.headers.get_content_charset() or "utf-8"
    except Exception as exc:
        log_file_access(
            source,
            operation='download',
            status='error',
            source='knowledge_url',
            error=type(exc).__name__,
        )
        raise

    suffix = Path(urlparse(source).path).suffix.lower()
    fmt = suffix.lstrip('.') or 'text'
    try:
        if suffix in {".xlsx", ".xls"}:
            df = pd.read_excel(io.BytesIO(data))
            knowledge = _knowledge_from_dataframe(df)
        elif suffix in {".csv", ".tsv"}:
            df = pd.read_csv(io.StringIO(data.decode(encoding)))
            knowledge = _knowledge_from_dataframe(df)
        else:
            knowledge = _knowledge_from_markdown(data.decode(encoding))
    except Exception as exc:
        log_file_access(
            source,
            operation='parse',
            status='error',
            source='knowledge_url',
            format=fmt,
            error=type(exc).__name__,
        )
        raise

    log_file_access(
        source,
        operation='download',
        status='success',
        source='knowledge_url',
        format=fmt,
        entries=len(knowledge),
        bytes=len(data),
    )
    return knowledge, None




def _should_refresh(source: str, force_refresh: bool) -> bool:
    if force_refresh:
        return True
    cached = _KNOWLEDGE_CACHE["data"]
    if cached is None:
        return True
    if _KNOWLEDGE_CACHE["source"] != source:
        return True
    ttl = max(int(getattr(config, "KNOWLEDGE_CACHE_TTL", 60)), 0)
    if ttl == 0:
        return True
    now = time.time()
    if now - float(_KNOWLEDGE_CACHE["timestamp"]) >= ttl:
        return True
    if not _is_url(source):
        try:
            current_mtime = Path(source).stat().st_mtime
        except FileNotFoundError:
            return True
        if _KNOWLEDGE_CACHE["file_mtime"] != current_mtime:
            return True
    return False


def _update_cache(source: str, knowledge: Dict[str, str], file_mtime: Optional[float]) -> None:
    _KNOWLEDGE_CACHE["data"] = knowledge
    _KNOWLEDGE_CACHE["source"] = source
    _KNOWLEDGE_CACHE["timestamp"] = time.time()
    _KNOWLEDGE_CACHE["file_mtime"] = file_mtime


def _read_source(source: str) -> Tuple[Dict[str, str], Optional[float]]:
    if _is_url(source):
        try:
            return _load_from_url(source)
        except URLError as exc:
            raise FileNotFoundError(f"Unable to load knowledge from {source}: {exc}") from exc
    return _load_from_local(Path(source))


def _resolve_source(path: Optional[str]) -> str:
    return path or config.KNOWLEDGE_SOURCE or config.KNOWLEDGE_TEMPLATE


def load_knowledge(path: Optional[str] = None, *, force_refresh: bool = False) -> Dict[str, str]:
    """Load key/value facts from a dynamic knowledge source with caching."""

    source = _resolve_source(path)
    refresh_required = _should_refresh(source, force_refresh)
    log_function_call(
        'load_knowledge',
        stage='start',
        source=str(source),
        force_refresh=force_refresh,
        refresh_required=refresh_required,
    )

    if refresh_required:
        try:
            knowledge, mtime = _read_source(source)
        except Exception:
            if source == config.KNOWLEDGE_TEMPLATE:
                raise
            fallback_source = config.KNOWLEDGE_TEMPLATE
            log_function_call(
                'load_knowledge',
                stage='fallback',
                source=str(source),
                fallback=str(fallback_source),
            )
            knowledge, mtime = _read_source(fallback_source)
            source = fallback_source
        _update_cache(source, knowledge, mtime)

    cached = _KNOWLEDGE_CACHE["data"]
    if not isinstance(cached, dict):
        raise ValueError("Knowledge cache corrupted")

    if "founded_year" not in cached:
        raise ValueError("Knowledge template must include a 'founded_year' entry.")

    final_source = _KNOWLEDGE_CACHE.get("source") or source
    log_function_call(
        'load_knowledge',
        stage='completed',
        source=str(final_source),
        refresh_required=refresh_required,
        entries=len(cached),
    )

    return dict(cached)




def _reset_cache_for_tests() -> None:  # pragma: no cover - used only in tests
    _KNOWLEDGE_CACHE["data"] = None
    _KNOWLEDGE_CACHE["source"] = None
    _KNOWLEDGE_CACHE["timestamp"] = 0.0
    _KNOWLEDGE_CACHE["file_mtime"] = None


