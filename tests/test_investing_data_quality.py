"""Data Quality Gate tests — missing data, stale data, sentinels, no NEUTRAL coercion."""

import datetime as dt

from investing import config, data_quality
from investing.data_quality import make_datapoint
from investing.schemas import DataSentinel, DataStatus


def _now():
    return dt.datetime.now(dt.timezone.utc)


def test_fresh_value_is_ok():
    dp = make_datapoint("p", 100.0, source="t", kind="quote", as_of=_now())
    assert dp.status == DataStatus.OK
    assert dp.age_seconds is not None and dp.age_seconds < 5


def test_old_value_is_stale():
    old = _now() - dt.timedelta(hours=2)
    dp = make_datapoint("p", 100.0, source="t", kind="quote", as_of=old)
    assert dp.status == DataStatus.STALE


def test_missing_value_is_missing_not_zero():
    dp = make_datapoint("p", None, source="t", kind="quote")
    assert dp.status == DataStatus.MISSING
    assert dp.value is None  # never coerced to a neutral number


def test_error_value_is_error():
    dp = make_datapoint("p", None, source="t", kind="quote", error="boom")
    assert dp.status == DataStatus.ERROR


def test_gate_blocks_on_missing_required():
    points = {
        "price": make_datapoint("price", None, source="t", kind="quote"),
        "bars": make_datapoint("bars", True, source="t", kind="daily_bars", as_of=_now()),
    }
    res = data_quality.evaluate(points, required=["price", "bars"])
    assert not res.can_enter
    assert "price" in res.missing
    assert res.sentinels["price"] == DataSentinel.DATA_INCOMPLETE.value


def test_gate_blocks_on_stale_required():
    old = _now() - dt.timedelta(days=2)
    points = {
        "price": make_datapoint("price", 10.0, source="t", kind="quote", as_of=old),
        "bars": make_datapoint("bars", True, source="t", kind="daily_bars", as_of=_now()),
    }
    res = data_quality.evaluate(points, required=["price", "bars"])
    assert not res.can_enter
    assert "price" in res.stale
    assert res.sentinels["price"] == DataSentinel.DATA_STALE.value


def test_gate_passes_when_all_required_ok():
    points = {
        "price": make_datapoint("price", 10.0, source="t", kind="quote", as_of=_now()),
        "bars": make_datapoint("bars", True, source="t", kind="daily_bars", as_of=_now()),
        "earnings_date": make_datapoint("e", "2026-09-01", source="t", kind="earnings", as_of=_now()),
    }
    res = data_quality.evaluate(points, required=["price", "bars", "earnings_date"])
    assert res.can_enter
    assert res.score >= config.MIN_DATA_QUALITY_FOR_ENTRY


def test_error_required_marks_source_unavailable():
    points = {"price": make_datapoint("price", None, source="yf", kind="quote", error="net")}
    res = data_quality.evaluate(points, required=["price"])
    assert res.sentinels["price"] == DataSentinel.SOURCE_UNAVAILABLE.value
