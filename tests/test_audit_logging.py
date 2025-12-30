import json
import os
import sys
from pathlib import Path

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import account_data, config, knowledge, pipeline


def _read_audit_entries(path: Path) -> list[dict]:
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        entries.append(json.loads(line))
    return entries


def test_audit_log_records_pipeline_events(monkeypatch, tmp_path):
    audit_path = tmp_path / 'audit.log'
    history_path = tmp_path / 'history.xlsx'
    monkeypatch.setattr(config, 'AUDIT_LOG_PATH', str(audit_path))
    monkeypatch.setattr(pipeline, 'PIPELINE_LOG_PATH', str(history_path))

    account_data.load_account_records.cache_clear()
    knowledge._reset_cache_for_tests()

    result = pipeline.run_pipeline('When were you founded?')
    assert 'reply' in result

    entries = _read_audit_entries(audit_path)
    assert entries, 'expected audit log entries'

    assert any(
        entry.get('event') == 'function_call'
        and entry['details'].get('function') == 'run_pipeline.start'
        for entry in entries
    )
    assert any(
        entry.get('event') == 'function_call'
        and entry['details'].get('function') == 'run_pipeline.end'
        and entry['details'].get('stage') == 'completed'
        for entry in entries
    )
    assert any(
        entry.get('event') == 'file_access'
        and entry['details'].get('source') == 'knowledge_local'
        and entry['details'].get('status') == 'success'
        for entry in entries
    )


def test_account_data_missing_file_logged(monkeypatch, tmp_path):
    audit_path = tmp_path / 'audit.log'
    monkeypatch.setattr(config, 'AUDIT_LOG_PATH', str(audit_path))

    account_data.load_account_records.cache_clear()

    missing_path = tmp_path / 'missing.xlsx'
    records = account_data.load_account_records(str(missing_path))
    assert records == {}

    entries = _read_audit_entries(audit_path)
    assert any(
        entry.get('event') == 'file_access'
        and entry['details'].get('path') == str(missing_path)
        and entry['details'].get('status') == 'missing'
        for entry in entries
    )
    assert any(
        entry.get('event') == 'function_call'
        and entry['details'].get('function') == 'load_account_records'
        and entry['details'].get('stage') == 'completed'
        for entry in entries
    )
