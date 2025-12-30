from app.time_window import parse_time_window


def test_iso_timestamp_parsed():
    res = parse_time_window("Failure at 2025-12-29T14:30")
    assert res["start"].startswith("2025-12-29T14:30")
    assert res["confidence"] >= 0.7


def test_relative_yesterday_low_confidence():
    res = parse_time_window("emails failing since yesterday afternoon")
    assert res["confidence"] <= 0.3
    assert res["end"] is None


def test_no_time_mentions_low_confidence():
    res = parse_time_window("emails failing")
    assert res["start"] is None
    assert res["confidence"] <= 0.2
