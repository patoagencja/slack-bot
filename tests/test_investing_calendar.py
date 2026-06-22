"""XNYS market-calendar tests — holidays, early closes, UTC timing, DST."""

import datetime as dt

from investing import market_calendar as mc


def test_weekend_not_trading():
    assert not mc.is_trading_day(dt.date(2025, 6, 21))  # Saturday
    assert not mc.is_trading_day(dt.date(2025, 6, 22))  # Sunday


def test_known_holidays_2025():
    assert not mc.is_trading_day(dt.date(2025, 12, 25))  # Christmas
    assert not mc.is_trading_day(dt.date(2025, 7, 4))    # Independence Day
    assert not mc.is_trading_day(dt.date(2025, 1, 1))    # New Year
    assert not mc.is_trading_day(dt.date(2025, 4, 18))   # Good Friday
    assert not mc.is_trading_day(dt.date(2025, 6, 19))   # Juneteenth
    assert not mc.is_trading_day(dt.date(2025, 11, 27))  # Thanksgiving


def test_regular_open_in_utc_during_edt():
    # 2025-07-07 is a Monday in EDT (UTC-4) -> 9:30 ET == 13:30 UTC
    o = mc.market_open_utc(dt.date(2025, 7, 7))
    assert o is not None
    assert (o.hour, o.minute) == (13, 30)


def test_regular_open_in_utc_during_est():
    # 2025-01-06 Monday in EST (UTC-5) -> 9:30 ET == 14:30 UTC
    o = mc.market_open_utc(dt.date(2025, 1, 6))
    assert (o.hour, o.minute) == (14, 30)


def test_early_close_day_after_thanksgiving():
    d = dt.date(2025, 11, 28)
    assert mc.is_early_close(d)
    close = mc.market_close_utc(d)
    # 13:00 ET in EST -> 18:00 UTC
    assert (close.hour, close.minute) == (18, 0)


def test_pre_open_brief_is_open_minus_lead():
    o = mc.market_open_utc(dt.date(2025, 7, 7))
    brief = mc.pre_open_brief_utc(dt.date(2025, 7, 7), lead_minutes=45)
    assert (o - brief) == dt.timedelta(minutes=45)


def test_next_pre_open_brief_skips_holiday_and_weekend():
    # Friday after close -> next brief is Monday (or next trading day) pre-open
    now = dt.datetime(2025, 7, 3, 23, 0, tzinfo=dt.timezone.utc)  # July 3 (early close) evening
    nxt = mc.next_pre_open_brief_utc(now)
    assert mc.is_trading_day(nxt.date())
    assert nxt > now
