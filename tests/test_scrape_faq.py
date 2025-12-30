from pathlib import Path

import pandas as pd

from tools.scrape_faq import SourceConfig, collect_entries, _diff_entries


def test_collect_entries_from_html_table(tmp_path):
    fixture = Path(__file__).resolve().parent / "fixtures" / "faq.html"
    cfg = SourceConfig(type="html-table", location=str(fixture), key_column="Key", value_column="Value")
    df = collect_entries([cfg])
    assert df.shape[0] == 2
    assert set(df["Key"]) == {"company_name", "founded_year"}


def test_diff_entries_detects_changes():
    old = pd.DataFrame({"Key": ["company_name"], "Value": ["Old"]})
    new = pd.DataFrame({"Key": ["company_name", "founded_year"], "Value": ["Aurora", "1990"]})
    diff = _diff_entries(old, new)
    assert diff["added"] == ["founded_year"]
    assert diff["changed"] == ["company_name"]
    assert diff["removed"] == []

