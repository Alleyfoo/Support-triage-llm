"""Customer service email pipeline."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


from .account_data import get_account_record
from .audit import log_function_call
from .config import (
    MODEL_BACKEND,
    MODEL_PATH,
    N_THREADS,
    CTX,
    TEMP,
    MAX_TOKENS,
    PIPELINE_LOG_PATH,
    OLLAMA_MODEL,
    OLLAMA_HOST,
    OLLAMA_TIMEOUT,
    OLLAMA_OPTIONS,
)
from .knowledge import load_knowledge
from .slm_llamacpp import generate_email_reply
from .slm_ollama import generate_email_reply_ollama

try:  # optional dependency
    from llama_cpp import Llama  # type: ignore
except Exception:  # pragma: no cover - llama_cpp is optional
    Llama = None  # type: ignore

_LLAMA = None

_KEY_CODE_REGEX = re.compile(r"\b([A-Z]{2,}-\d{2,})\b", re.IGNORECASE)

_KEYWORD_MAP: List[tuple[str, str]] = [
    ("company name", "company_name"),
    ("who are you", "company_name"),
    ("founded", "founded_year"),
    ("established", "founded_year"),
    ("history", "founded_year"),
    ("where", "headquarters"),
    ("based", "headquarters"),
    ("headquarter", "headquarters"),
    ("support hours", "support_hours"),
    ("opening hours", "support_hours"),
    ("warranty", "warranty_policy"),
    ("guarantee", "warranty_policy"),
    ("return", "return_policy"),
    ("refund", "return_policy"),
    ("shipping", "shipping_time"),
    ("ship", "shipping_time"),
    ("deliver", "shipping_time"),
    ("loyalty", "loyalty_program"),
    ("rewards", "loyalty_program"),
    ("perks", "loyalty_program"),
    ("contact", "support_email"),
    ("email", "support_email"),
    ("support team", "support_email"),
    ("premium support", "premium_support"),
    ("sla", "premium_support"),
    ("regular key", "account_regular_key"),
    ("account key", "account_regular_key"),
    ("my key", "account_regular_key"),
    ("secret key", "account_security_notice"),
    ("secret code", "account_security_notice"),
    ("confidential key", "account_security_notice"),
    ("share secret", "account_security_notice"),
]


_ACCOUNT_FIELD_MAP: Dict[str, str] = {
    'regular_key': 'account_regular_key',
}
_ACCOUNT_SECRET_FIELD = 'secret_key'
_ACCOUNT_BANNED_KEYS = {'account_secret_key'}
_SECURITY_NOTICE_KEY = 'account_security_notice'
_SECURITY_NOTICE_VALUE = (
    'For security reasons we cannot disclose secret keys or other customer data.'
)
_ACCOUNT_VERIFIED_KEY = 'account_identity_status'
_ACCOUNT_VERIFIED_VALUE = (
    'Thanks for confirming your shared secret. Your identity is verified.'
)

_REPLY_PREFIX = 're:'

_KEY_CODE_PATTERN = re.compile(r"\b([A-Z]{2,}-\d{2,})\b", re.IGNORECASE)


def _dedupe_preserve(items: List[str]) -> List[str]:
    """Return a list with duplicates removed while preserving order."""

    seen = set()
    unique: List[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _detect_keyword_keys(email_text: str) -> List[str]:
    """Infer expected keys using simple keyword heuristics."""

    lower = email_text.lower()
    seen: List[str] = []
    for keyword, key in _KEYWORD_MAP:
        if keyword in lower and key not in seen:
            seen.append(key)
    return seen


def _find_key_code_keys(email_text: str, knowledge: Dict[str, str]) -> List[str]:
    """Return knowledge keys that correspond to explicit key codes."""

    matches: List[str] = []
    for match in _KEY_CODE_PATTERN.finditer(email_text):
        code = match.group(1).upper()
        key = f"key_code_{code}"
        if key in knowledge and key not in matches:
            matches.append(key)
    return matches


def _resolve_expected_keys(
    email_text: str,
    knowledge: Dict[str, str],
    hints: Optional[List[str]] = None,
) -> Tuple[List[str], Dict[str, str]]:
    """Compute expected knowledge keys and their canonical answers."""

    hints_list = _dedupe_preserve([str(hint) for hint in hints]) if hints else []
    expected_keys: List[str] = []
    answers: Dict[str, str] = {}

    def add_key(key: str) -> None:
        if key and key not in expected_keys:
            expected_keys.append(key)
            value = knowledge.get(key)
            if value:
                answers[key] = value

    for key in _find_key_code_keys(email_text, knowledge):
        add_key(key)

    if hints_list:
        for key in hints_list:
            add_key(key)
        heuristic_keys: List[str] = []
    else:
        heuristic_keys = _detect_keyword_keys(email_text)

    for key in heuristic_keys:
        add_key(key)

    return expected_keys, answers


def _load_llama():
    """Lazily load llama-cpp model using environment configuration."""

    global _LLAMA
    if _LLAMA is None and Llama is not None and MODEL_PATH:
        try:  # pragma: no cover - exercised only when llama_cpp is installed
            _LLAMA = Llama(
                model_path=MODEL_PATH,
                n_threads=N_THREADS,
                n_ctx=CTX,
            )
        except Exception:
            _LLAMA = None
    return _LLAMA


def detect_expected_keys(
    email_text: str,
    hints: Optional[List[str]] = None,
    knowledge: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Infer which knowledge keys the email is asking about."""

    if knowledge is None:
        knowledge = load_knowledge()
    expected_keys, _ = _resolve_expected_keys(email_text, knowledge, hints=hints)
    return expected_keys


def _merge_unique(*sequences: Optional[List[str]]) -> List[str]:
    """Return a flattened list with stable order and duplicates removed."""

    combined: List[str] = []
    for seq in sequences:
        if not seq:
            continue
        for item in seq:
            if item not in combined:
                combined.append(item)
    return combined


def _detect_key_codes(email_text: str, knowledge: Dict[str, str]) -> List[str]:
    """Extract explicit key codes (e.g. ``AG-445``) referenced in the email."""

    if not email_text:
        return []

    codes: List[str] = []
    for match in _KEY_CODE_REGEX.findall(email_text):
        key = f"key_code_{match.upper()}"
        if key in knowledge and key not in codes:
            codes.append(key)
    return codes


def _log_pipeline_run(
    email_text: str,
    reply: str,
    expected_keys: List[str],
    answers: Dict[str, Any],
    evaluation: Dict[str, Any],
) -> None:
    """Append the latest pipeline result to the Excel history file."""

    log_path = PIPELINE_LOG_PATH
    if not log_path:
        return

    path = Path(log_path)

    from datetime import datetime
    record = {
        "email": email_text,
        "reply": reply,
        "expected_keys": json.dumps(expected_keys, ensure_ascii=False),
        "answers": json.dumps(answers, ensure_ascii=False),
        "score": evaluation.get("score"),
        "matched": json.dumps(evaluation.get("matched", []), ensure_ascii=False),
        "missing": json.dumps(evaluation.get("missing", []), ensure_ascii=False),
        "processed_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "backend": MODEL_BACKEND,
        "model": OLLAMA_MODEL if MODEL_BACKEND == "ollama" else (MODEL_PATH or ""),
    }

    try:
        import pandas as pd  # type: ignore
    except Exception:  # pragma: no cover - pandas optional at runtime
        return

    try:
        if path.exists():
            try:
                existing = pd.read_excel(path)
            except Exception:
                existing = None
            if existing is not None:
                df = pd.concat([existing, pd.DataFrame([record])], ignore_index=True)
            else:
                df = pd.DataFrame([record])
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            df = pd.DataFrame([record])

        # Atomic write: write to temp file then replace
        import os, tempfile
        with tempfile.NamedTemporaryFile(
            mode="w+b", suffix=".xlsx", delete=False, dir=str(path.parent)
        ) as tmp:
            tmp_path = Path(tmp.name)
        try:
            with pd.ExcelWriter(tmp_path, engine="openpyxl") as writer:
                df.to_excel(writer, index=False)
            os.replace(tmp_path, path)
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception:  # pragma: no cover - avoid breaking pipeline on IO errors
        return


def evaluate_reply(
    email_text: str,
    reply_text: str,
    expected_keys: List[str],
    knowledge: Dict[str, str],
) -> Dict[str, Any]:
    """Compare reply against expected knowledge entries and score coverage."""

    if not expected_keys:
        return {"score": 1.0, "matched": [], "missing": []}

    reply_lower = reply_text.lower()
    matched: List[str] = []
    missing: List[str] = []
    for key in expected_keys:
        value = knowledge.get(key, "")
        if value and value.lower() in reply_lower:
            matched.append(key)
        else:
            missing.append(key)

    score = len(matched) / len(expected_keys) if expected_keys else 1.0
    return {"score": round(score, 2), "matched": matched, "missing": missing}


def run_pipeline(email_text: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Generate a reply and evaluate how well it addresses the email."""

    metadata_dict: Dict[str, Any] = dict(metadata) if metadata else {}
    lang = str(metadata_dict.get("language", "")).strip().lower() if metadata_dict else ""

    log_function_call(
        "run_pipeline.start",
        metadata_keys=sorted(metadata_dict.keys()),
        email_chars=len(email_text),
        language=lang or None,
    )

    # Choose language-specific knowledge source when provided
    selected_source: Optional[str] = None
    try:
        from . import config as _cfg
        if lang == "fi" and getattr(_cfg, "KNOWLEDGE_SOURCE_FI", None):
            selected_source = _cfg.KNOWLEDGE_SOURCE_FI
        elif lang in {"sv", "se"} and getattr(_cfg, "KNOWLEDGE_SOURCE_SV", None):
            selected_source = _cfg.KNOWLEDGE_SOURCE_SV
        elif lang == "en" and getattr(_cfg, "KNOWLEDGE_SOURCE_EN", None):
            selected_source = _cfg.KNOWLEDGE_SOURCE_EN
    except Exception:
        selected_source = None

    knowledge = load_knowledge(path=selected_source)
    email_lower = email_text.lower()

    subject_value = metadata_dict.get("subject")
    if subject_value is not None:
        subject_normalised = str(subject_value).strip()
        if subject_normalised.lower().startswith(_REPLY_PREFIX):
            reply_text = (
                "Subject indicates a follow-up (prefixed with 'Re:'). Forward to a human agent."
            )
            expected_keys: List[str] = []
            answers: Dict[str, str] = {}
            evaluation = {"score": 0.0, "matched": [], "missing": []}
            log_function_call(
                "run_pipeline.end",
                stage="subject_reply",
                evaluation_score=0.0,
                status="forward_to_human",
            )
            _log_pipeline_run(email_text, reply_text, expected_keys, answers, evaluation)
            return {
                "reply": reply_text,
                "expected_keys": expected_keys,
                "answers": answers,
                "evaluation": evaluation,
            }

    customer_email: Optional[str] = None
    for key in ("customer_email", "sender_email", "from_email"):
        candidate = metadata_dict.get(key)
        if candidate:
            customer_email = str(candidate)
            break

    account_record = get_account_record(customer_email) if customer_email else {}

    identity_verified = False
    secret_value_raw = account_record.get(_ACCOUNT_SECRET_FIELD)
    secret_value: Optional[str] = None
    if secret_value_raw is not None:
        secret_value = str(secret_value_raw).strip()
        if secret_value and secret_value.lower() != 'nan':
            if secret_value.lower() in email_lower:
                identity_verified = True

    account_knowledge: Dict[str, str] = {
        dest: account_record[source]
        for source, dest in _ACCOUNT_FIELD_MAP.items()
        if account_record.get(source)
    }
    if _SECURITY_NOTICE_KEY not in knowledge:
        knowledge[_SECURITY_NOTICE_KEY] = _SECURITY_NOTICE_VALUE
    if account_knowledge:
        account_knowledge.setdefault(_SECURITY_NOTICE_KEY, _SECURITY_NOTICE_VALUE)
        if identity_verified:
            account_knowledge[_ACCOUNT_VERIFIED_KEY] = _ACCOUNT_VERIFIED_VALUE
        knowledge.update(account_knowledge)
    elif identity_verified:
        knowledge[_ACCOUNT_VERIFIED_KEY] = _ACCOUNT_VERIFIED_VALUE

    hints_list: Optional[List[str]] = None
    hints_source: Optional[Any] = None
    if metadata_dict:
        hints_source = metadata_dict.get("expected_keys") or metadata_dict.get("hints")

    if hints_source is not None:
        if isinstance(hints_source, list):
            raw_hints = hints_source
        else:
            raw_hints = [hints_source]
        normalised_hints: List[str] = []
        for key in raw_hints:
            key_str = str(key)
            if key_str not in normalised_hints:
                normalised_hints.append(key_str)
        hints_list = [key for key in normalised_hints if key not in _ACCOUNT_BANNED_KEYS]

    key_code_keys = _detect_key_codes(email_text, knowledge)
    if key_code_keys:
        expected_keys = _merge_unique(key_code_keys, hints_list)
    else:
        expected_keys = detect_expected_keys(email_text, hints=hints_list)
    expected_keys, canonical_answers = _resolve_expected_keys(
        email_text, knowledge, hints=hints_list
    )
    expected_keys = [key for key in expected_keys if key not in _ACCOUNT_BANNED_KEYS]
    canonical_answers = {
        key: value
        for key, value in canonical_answers.items()
        if key not in _ACCOUNT_BANNED_KEYS
    }
    if identity_verified:
        canonical_answers.setdefault(_ACCOUNT_VERIFIED_KEY, _ACCOUNT_VERIFIED_VALUE)
        if _ACCOUNT_VERIFIED_KEY not in expected_keys:
            expected_keys.append(_ACCOUNT_VERIFIED_KEY)

    needs_human = (
        not expected_keys
        and not hints_list
        and not account_knowledge
        and not key_code_keys
    )

    if needs_human:
        answers = canonical_answers.copy()
        evaluation = {"score": 0.0, "matched": [], "missing": []}
        reply = ""
        log_function_call(
            "run_pipeline.end",
            stage="needs_human",
            evaluation_score=0.0,
            status="human_review",
        )
        _log_pipeline_run(email_text, reply, expected_keys, answers, evaluation)
        return {
            "reply": reply,
            "expected_keys": expected_keys,
            "answers": answers,
            "evaluation": evaluation,
            "human_review": True,
        }

    if MODEL_BACKEND == "ollama":
        generation = generate_email_reply_ollama(
            email_text,
            knowledge=knowledge,
            expected_keys=expected_keys,
            model=OLLAMA_MODEL,
            host=OLLAMA_HOST,
            temperature=TEMP,
            max_tokens=MAX_TOKENS,
            raw_options=OLLAMA_OPTIONS,
            timeout=OLLAMA_TIMEOUT,
            language=lang if lang else None,
        )
    else:
        llama = _load_llama() if MODEL_BACKEND == "llama.cpp" else None
        generation = generate_email_reply(
            email_text,
            knowledge=knowledge,
            expected_keys=expected_keys,
            llama=llama,
            temp=TEMP,
            max_tokens=MAX_TOKENS,
            language=lang if lang else None,
        )

    reply = generation.get("reply", "")
    answers = generation.get("answers", {})
    if not isinstance(answers, dict):
        answers = {}
    else:
        answers = {str(k): str(v) for k, v in answers.items()}
    answers.update(canonical_answers)
    answers = {k: v for k, v in answers.items() if k not in _ACCOUNT_BANNED_KEYS}
    evaluation = evaluate_reply(email_text, reply, expected_keys, knowledge)

    log_function_call(
        "run_pipeline.end",
        stage="completed",
        evaluation_score=evaluation.get("score"),
        expected_keys=len(expected_keys),
        backend=MODEL_BACKEND,
        language=lang or None,
        knowledge_source=selected_source or 'auto',
    )

    _log_pipeline_run(email_text, reply, expected_keys, answers, evaluation)

    return {
        "reply": reply,
        "expected_keys": expected_keys,
        "answers": answers,
        "evaluation": evaluation,
    }


def run_pipeline_like_this() -> Dict[str, Any]:  # pragma: no cover - example helper
    example = (
        "Hello, could you tell me when your company was founded and whether you offer premium support?"
    )
    return run_pipeline(example)

