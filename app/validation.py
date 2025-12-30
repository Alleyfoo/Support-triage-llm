"""Schema validation helpers for LLM outputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict

import jsonschema

SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "schemas"


class SchemaValidationError(RuntimeError):
    pass


def load_schema(name: str) -> Dict[str, Any]:
    """Load a JSON schema by filename (relative to schemas/)."""
    path = SCHEMAS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Schema not found: {path}")
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def validate_payload(payload: Dict[str, Any], schema_name: str) -> None:
    """Validate a payload against a named schema or raise SchemaValidationError."""
    schema = load_schema(schema_name)
    try:
        jsonschema.validate(payload, schema)
    except jsonschema.ValidationError as exc:  # pragma: no cover - exercised in integration paths
        raise SchemaValidationError(str(exc)) from exc


def validate_with_retry(
    payload: Dict[str, Any],
    schema_name: str,
    fixer: Callable[[Dict[str, Any]], Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """
    Validate payload; optionally call fixer once to repair and revalidate.

    fixer receives the invalid payload and must return a new payload.
    """
    try:
        validate_payload(payload, schema_name)
        return payload
    except SchemaValidationError:
        if not fixer:
            raise
        candidate = fixer(payload)
        validate_payload(candidate, schema_name)
        return candidate
