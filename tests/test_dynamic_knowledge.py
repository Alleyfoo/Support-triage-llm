import os
from pathlib import Path

import pytest

from app import config
from app import knowledge


MARKDOWN_TEMPLATE = """
| Key | Value |
| --- | ----- |
| founded_year | 2030 |
| company_name | Dynamic Aurora |
""".strip()


@pytest.fixture(autouse=True)
def reset_knowledge_cache():
    knowledge._reset_cache_for_tests()
    yield
    knowledge._reset_cache_for_tests()


def test_loads_from_custom_source(tmp_path, monkeypatch):
    source = tmp_path / "live.md"
    source.write_text(MARKDOWN_TEMPLATE, encoding="utf-8")

    monkeypatch.setattr(config, "KNOWLEDGE_SOURCE", str(source), raising=False)
    monkeypatch.setattr(config, "KNOWLEDGE_CACHE_TTL", 60, raising=False)

    data = knowledge.load_knowledge(force_refresh=True)
    assert data["founded_year"] == "2030"
    assert data["company_name"] == "Dynamic Aurora"


def test_cache_refreshes_when_file_changes(tmp_path, monkeypatch):
    source = tmp_path / "live.md"
    source.write_text(MARKDOWN_TEMPLATE.replace("2030", "2031"), encoding="utf-8")

    monkeypatch.setattr(config, "KNOWLEDGE_SOURCE", str(source), raising=False)
    monkeypatch.setattr(config, "KNOWLEDGE_CACHE_TTL", 3600, raising=False)

    first = knowledge.load_knowledge(force_refresh=True)
    assert first["founded_year"] == "2031"

    source.write_text(MARKDOWN_TEMPLATE.replace("2030", "2032"), encoding="utf-8")
    os.utime(source, (source.stat().st_atime + 5, source.stat().st_mtime + 5))

    second = knowledge.load_knowledge()
    assert second["founded_year"] == "2032"


def test_zero_ttl_always_reload(tmp_path, monkeypatch):
    source = tmp_path / "live.md"
    source.write_text(MARKDOWN_TEMPLATE.replace("2030", "2040"), encoding="utf-8")

    monkeypatch.setattr(config, "KNOWLEDGE_SOURCE", str(source), raising=False)
    monkeypatch.setattr(config, "KNOWLEDGE_CACHE_TTL", 0, raising=False)

    initial = knowledge.load_knowledge()
    assert initial["founded_year"] == "2040"

    source.write_text(MARKDOWN_TEMPLATE.replace("2030", "2041"), encoding="utf-8")

    updated = knowledge.load_knowledge()
    assert updated["founded_year"] == "2041"
