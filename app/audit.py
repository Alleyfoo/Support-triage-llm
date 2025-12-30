"""Lightweight audit logging helpers."""

from __future__ import annotations

import getpass
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from . import config


def _serialise(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(key): _serialise(val) for key, val in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_serialise(item) for item in value]
    return str(value)


def _resolve_user() -> str:
    try:
        user = getpass.getuser()
        if user:
            return user
    except Exception:
        pass
    for env_name in ("USER", "USERNAME", "LOGNAME"):
        env_value = os.environ.get(env_name)
        if env_value:
            return env_value
    return "unknown"


def _write_record(path: Path, record: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.write("\n")
    except Exception:
        # Swallow audit logging failures so core functionality keeps working.
        return


def log_event(event: str, *, details: Dict[str, Any] | None = None, severity: str = "info") -> None:
    path_value = getattr(config, "AUDIT_LOG_PATH", "")
    if not path_value:
        return

    record: Dict[str, Any] = {
        "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "event": event,
        "severity": severity,
        "user": _resolve_user(),
    }
    if details:
        record["details"] = _serialise(details)

    _write_record(Path(path_value), record)


def log_function_call(function: str, **metadata: Any) -> None:
    details: Dict[str, Any] = {"function": function}
    if metadata:
        details.update({key: _serialise(value) for key, value in metadata.items()})
    log_event("function_call", details=details)


def log_file_access(path: str | os.PathLike[str], *, operation: str, status: str = "success", **metadata: Any) -> None:
    if isinstance(path, bytes):
        try:
            raw_path = path.decode("utf-8", "ignore")
        except Exception:
            raw_path = repr(path)
    else:
        raw_path = str(path)

    if raw_path.startswith(("http://", "https://")):
        resolved_path = raw_path
    else:
        resolved_path = str(Path(raw_path))

    details: Dict[str, Any] = {
        "path": resolved_path,
        "operation": operation,
        "status": status,
    }
    if metadata:
        details.update({key: _serialise(value) for key, value in metadata.items()})
    log_event("file_access", details=details)


def log_exception(event: str, *, error: Exception, **metadata: Any) -> None:
    details: Dict[str, Any] = {"error": type(error).__name__}
    if metadata:
        details.update({key: _serialise(value) for key, value in metadata.items()})
    log_event(event, details=details, severity="error")

