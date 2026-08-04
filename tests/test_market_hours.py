from datetime import datetime

from zoneinfo import ZoneInfo

from app.services.market_hours import ET, is_rth

UTC = ZoneInfo("UTC")


def test_rth_weekday_during_session():
    # Tuesday 2026-07-21 10:00 ET
    assert is_rth(datetime(2026, 7, 21, 10, 0, tzinfo=ET)) is True


def test_rth_weekday_at_open():
    assert is_rth(datetime(2026, 7, 21, 9, 30, tzinfo=ET)) is True


def test_rth_weekday_before_open():
    assert is_rth(datetime(2026, 7, 21, 9, 29, tzinfo=ET)) is False


def test_rth_weekday_at_close():
    assert is_rth(datetime(2026, 7, 21, 16, 0, tzinfo=ET)) is False


def test_rth_weekday_after_close():
    assert is_rth(datetime(2026, 7, 21, 16, 1, tzinfo=ET)) is False


def test_rth_saturday():
    assert is_rth(datetime(2026, 7, 25, 12, 0, tzinfo=ET)) is False


def test_rth_sunday():
    assert is_rth(datetime(2026, 7, 26, 12, 0, tzinfo=ET)) is False


def test_rth_accepts_utc_input():
    # 14:00 UTC = 10:00 ET on a Tuesday in July (EDT)
    assert is_rth(datetime(2026, 7, 21, 14, 0, tzinfo=UTC)) is True
