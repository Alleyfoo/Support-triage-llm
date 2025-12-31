from datetime import datetime, timezone

from app.time_window import parse_time_window


def test_iso_timestamp_parsed():
    res = parse_time_window("Failure at 2025-12-29T14:30", now=datetime(2025, 12, 30, tzinfo=timezone.utc))
    assert res["start"].startswith("2025-12-29T14:30")
    assert res["end"].startswith("2025-12-29T16:30")
    assert res["confidence"] >= 0.7


def test_relative_yesterday_low_confidence():
    res = parse_time_window("emails failing since yesterday afternoon", now=datetime(2025, 12, 30, tzinfo=timezone.utc))
    assert res["confidence"] <= 0.4
    assert res["start"] is None
    assert res["end"] is None


def test_no_time_mentions_low_confidence():
    res = parse_time_window("emails failing", now=datetime(2025, 12, 30, tzinfo=timezone.utc))
    assert res["start"] is None
    assert res["confidence"] <= 0.2


def test_date_only_parses_full_day():
    res = parse_time_window("Issue observed on 2025-05-01", now=datetime(2025, 12, 30, tzinfo=timezone.utc))
    assert res["start"].startswith("2025-05-01T00:00:00Z")
    assert res["end"].startswith("2025-05-02T12:00:00Z")
    assert res["confidence"] >= 0.5


def test_month_day_with_time():
    res = parse_time_window("May 1 at 10:45 UTC we saw failures", now=datetime(2025, 5, 2, tzinfo=timezone.utc))
    assert res["start"].endswith("10:45:00Z")
    assert res["end"].endswith("12:45:00Z")
    assert res["confidence"] >= 0.5


def test_clock_with_relative_yesterday_anchors_to_anchor_date():
    anchor = datetime(2025, 12, 31, 12, 0, tzinfo=timezone.utc)
    res = parse_time_window("Since 18:00 UTC yesterday we see issues", now=anchor)
    assert res["start"] == "2025-12-30T18:00:00Z"
    assert res["reason"] == "parsed_from_text"


def test_clock_without_date_anchors_to_today():
    anchor = datetime(2025, 12, 31, 12, 0, tzinfo=timezone.utc)
    res = parse_time_window("Around 07:05 UTC the errors started", now=anchor)
    assert res["start"] == "2025-12-31T07:05:00Z"
    assert res["reason"] == "parsed_from_text"
