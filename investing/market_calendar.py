"""
investing/market_calendar.py — XNYS (NYSE) trading calendar, computed in UTC.

The pre-US-open brief must be scheduled relative to the *actual* XNYS open
(open − 45 min by default), accounting for weekends, US market holidays, early
closes and the US/Europe DST mismatch. All public functions return tz-aware UTC
datetimes. Uses zoneinfo for America/New_York so DST is handled correctly.
"""

from __future__ import annotations

import datetime as _dt
from functools import lru_cache
from zoneinfo import ZoneInfo

from . import config

ET = ZoneInfo("America/New_York")
UTC = _dt.timezone.utc

REGULAR_OPEN = _dt.time(9, 30)
REGULAR_CLOSE = _dt.time(16, 0)
EARLY_CLOSE = _dt.time(13, 0)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> _dt.date:
    """nth (1-based) ``weekday`` (Mon=0) of month; n<0 counts from the end."""
    if n > 0:
        d = _dt.date(year, month, 1)
        offset = (weekday - d.weekday()) % 7
        return d + _dt.timedelta(days=offset + 7 * (n - 1))
    # last
    if month == 12:
        d = _dt.date(year, 12, 31)
    else:
        d = _dt.date(year, month + 1, 1) - _dt.timedelta(days=1)
    offset = (d.weekday() - weekday) % 7
    return d - _dt.timedelta(days=offset)


def _easter(year: int) -> _dt.date:
    """Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return _dt.date(year, month, day)


def _observed(d: _dt.date) -> _dt.date:
    if d.weekday() == 5:        # Saturday -> Friday
        return d - _dt.timedelta(days=1)
    if d.weekday() == 6:        # Sunday -> Monday
        return d + _dt.timedelta(days=1)
    return d


@lru_cache(maxsize=32)
def holidays(year: int) -> frozenset[_dt.date]:
    hs = {
        _observed(_dt.date(year, 1, 1)),                       # New Year's
        _nth_weekday(year, 1, 0, 3),                           # MLK
        _nth_weekday(year, 2, 0, 3),                           # Washington
        _easter(year) - _dt.timedelta(days=2),                 # Good Friday
        _nth_weekday(year, 5, 0, -1),                          # Memorial Day
        _observed(_dt.date(year, 7, 4)),                       # Independence Day
        _nth_weekday(year, 9, 0, 1),                           # Labor Day
        _nth_weekday(year, 11, 3, 4),                          # Thanksgiving
        _observed(_dt.date(year, 12, 25)),                     # Christmas
    }
    if year >= 2022:
        hs.add(_observed(_dt.date(year, 6, 19)))               # Juneteenth
    return frozenset(hs)


def early_closes(year: int) -> frozenset[_dt.date]:
    closes = set()
    # Day after Thanksgiving
    closes.add(_nth_weekday(year, 11, 3, 4) + _dt.timedelta(days=1))
    # July 3 (if a weekday)
    j3 = _dt.date(year, 7, 3)
    if j3.weekday() < 5:
        closes.add(j3)
    # Christmas Eve (if a weekday)
    c24 = _dt.date(year, 12, 24)
    if c24.weekday() < 5:
        closes.add(c24)
    return frozenset(c for c in closes if c not in holidays(year))


def is_trading_day(d: _dt.date) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


def is_early_close(d: _dt.date) -> bool:
    return d in early_closes(d.year)


def _et_to_utc(d: _dt.date, t: _dt.time) -> _dt.datetime:
    return _dt.datetime.combine(d, t, tzinfo=ET).astimezone(UTC)


def market_open_utc(d: _dt.date) -> _dt.datetime | None:
    return _et_to_utc(d, REGULAR_OPEN) if is_trading_day(d) else None


def market_close_utc(d: _dt.date) -> _dt.datetime | None:
    if not is_trading_day(d):
        return None
    return _et_to_utc(d, EARLY_CLOSE if is_early_close(d) else REGULAR_CLOSE)


def next_trading_day(d: _dt.date) -> _dt.date:
    nxt = d + _dt.timedelta(days=1)
    while not is_trading_day(nxt):
        nxt += _dt.timedelta(days=1)
    return nxt


def pre_open_brief_utc(d: _dt.date, lead_minutes: int = config.PRE_OPEN_BRIEF_LEAD_MIN) -> _dt.datetime | None:
    """UTC time to fire the pre-open brief on trading day ``d`` (open − lead)."""
    open_utc = market_open_utc(d)
    return open_utc - _dt.timedelta(minutes=lead_minutes) if open_utc else None


def next_pre_open_brief_utc(now_utc: _dt.datetime | None = None,
                            lead_minutes: int = config.PRE_OPEN_BRIEF_LEAD_MIN) -> _dt.datetime:
    """Next upcoming pre-open brief time at/after ``now_utc``."""
    now_utc = now_utc or _dt.datetime.now(UTC)
    d = now_utc.date()
    for _ in range(10):
        if is_trading_day(d):
            t = pre_open_brief_utc(d, lead_minutes)
            if t and t > now_utc:
                return t
        d = d + _dt.timedelta(days=1)
    raise RuntimeError("no trading day found in horizon")
