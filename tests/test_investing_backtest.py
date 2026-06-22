"""Backtest outcome-tracking tests."""

from investing import backtest


def _bars(seq):
    # seq of (high, low, close) -> bar dicts
    return [{"high": h, "low": l, "close": c, "open": c} for (h, l, c) in seq]


def test_target_hit_outcome():
    # entry 100, stop 95 (risk 5). Price runs to 110 (T1=110 -> +2R)
    bars = _bars([(102, 99, 101), (106, 100, 105), (111, 104, 110)])
    out = backtest.compute_outcome(100, 95, [110, 115, 120], bars)
    assert out["hit_target_1"]
    assert out["time_to_target"] == 3
    assert out["mfe"] >= 2.0
    assert not out["hit_stop"]


def test_stop_hit_outcome():
    bars = _bars([(101, 94, 96), (97, 90, 92)])  # low 94 <= stop 95 on bar 1
    out = backtest.compute_outcome(100, 95, [110], bars)
    assert out["hit_stop"]
    assert out["r_multiple"] == -1.0


def test_no_hit_uses_final_close():
    bars = _bars([(101, 99, 100), (102, 99, 101.5)])  # never hits T1=110 or stop 95
    out = backtest.compute_outcome(100, 95, [110], bars)
    assert not out["hit_stop"] and not out["hit_target_1"]
    # final close 101.5 -> (101.5-100)/5 = 0.3R
    assert out["r_multiple"] == 0.3


def test_horizons_only_record_when_enough_bars(tmp_path):
    db = str(tmp_path / "bt.db")
    bars = _bars([(101, 99, 100)] * 12)  # only 12 sessions
    ids = backtest.record_outcomes_at_horizons(1, "NVDA", 100, 95, [110], bars,
                                               setup_type="BREAKOUT", db_path=db)
    # horizons 5 and 10 recorded; 20/40/60 skipped
    assert len(ids) == 2
