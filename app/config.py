import os
from pathlib import Path
from typing import Optional


def _parse_int_default(default: int, *names: str) -> int:
    for name in names:
        raw = os.environ.get(name)
        if raw is None or raw == "":
            continue
        try:
            return int(raw)
        except ValueError:
            continue
    return default


def _parse_float_default(default: float, *names: str) -> float:
    for name in names:
        raw = os.environ.get(name)
        if raw is None or raw == "":
            continue
        try:
            return float(raw)
        except ValueError:
            continue
    return default


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


MODEL_BACKEND = (os.environ.get("MODEL_BACKEND") or "llama.cpp").lower()
MODEL_PATH = os.environ.get("MODEL_PATH")
N_THREADS = _parse_int_default(8, "N_THREADS")
CTX = _parse_int_default(2048, "CTX")
TEMP = _parse_float_default(0.0, "MODEL_TEMP", "TEMP")
MAX_TOKENS = _parse_int_default(512, "MODEL_MAX_TOKENS", "MAX_TOKENS")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_TIMEOUT = _parse_float_default(60.0, "OLLAMA_TIMEOUT")
OLLAMA_OPTIONS = os.environ.get("OLLAMA_OPTIONS")
REQUIRE_API_KEY = (os.environ.get("REQUIRE_API_KEY") or "false").lower() == "true"
INGEST_API_KEY: Optional[str] = _require_env("INGEST_API_KEY") if REQUIRE_API_KEY else os.environ.get("INGEST_API_KEY")
KNOWLEDGE_TEMPLATE = os.environ.get(
    "KNOWLEDGE_TEMPLATE",
    str(Path(__file__).resolve().parent.parent / "docs" / "customer_service_template.md"),
)
KNOWLEDGE_SOURCE = os.environ.get("KNOWLEDGE_SOURCE")
KNOWLEDGE_SOURCE_FI = os.environ.get("KNOWLEDGE_SOURCE_FI")
KNOWLEDGE_SOURCE_SV = os.environ.get("KNOWLEDGE_SOURCE_SV")
KNOWLEDGE_SOURCE_EN = os.environ.get("KNOWLEDGE_SOURCE_EN")
KNOWLEDGE_CACHE_TTL = _parse_int_default(60, "KNOWLEDGE_CACHE_TTL")
PIPELINE_LOG_PATH = os.environ.get(
    "PIPELINE_LOG_PATH",
    str(Path(__file__).resolve().parent.parent / "data" / "pipeline_history.xlsx"),
)

AUDIT_LOG_PATH = os.environ.get(
    "AUDIT_LOG_PATH",
    str(Path(__file__).resolve().parent.parent / "data" / "audit.log"),
)

ACCOUNT_DATA_PATH = os.environ.get(
    "ACCOUNT_DATA_PATH",
    str(Path(__file__).resolve().parent.parent / "data" / "account_records.xlsx"),
)
