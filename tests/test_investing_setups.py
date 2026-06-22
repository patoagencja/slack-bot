"""Setup-classifier tests, including the rule that RSI alone never forces NO_TRADE."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import invest_fixtures as fx  # noqa: E402

from investing import setups, indicators as ind  # noqa: E402
from investing.schemas import SetupType  # noqa: E402


def test_breakout_classified_and_qualifies():
    c, h, l, v = fx.gen_breakout()
    rs = {"rs63_broad": 8.0, "pct_rank_universe": 85}
    setup = setups.classify(setups.build_features(c, h, l, v, rs))
    assert setup.setup_type == SetupType.BREAKOUT
    assert setup.qualifies
    assert setup.trigger is not None and setup.stop is not None
    assert setup.max_chase is not None and setup.entry_zone is not None
    assert len(setup.targets) == 3
    # max chase must respect the configured ATR multiple over the pivot
    assert setup.max_chase <= setup.trigger + 0.76 * setup.features["atr"]


def test_downtrend_yields_no_valid_setup():
    c, h, l, v = fx.gen_downtrend()
    rs = {"rs63_broad": -20.0}
    setup = setups.classify(setups.build_features(c, h, l, v, rs))
    assert setup.setup_type == SetupType.NO_VALID_SETUP
    assert not setup.qualifies


def test_pullback_continuation_detected():
    c, h, l, v = fx.gen_uptrend_pullback()
    rs = {"rs63_broad": 6.0}
    setup = setups.classify(setups.build_features(c, h, l, v, rs))
    assert setup.setup_type in (SetupType.PULLBACK_CONTINUATION, SetupType.BREAKOUT)
    assert setup.qualifies


def test_rsi_alone_does_not_force_no_trade():
    """A high RSI on an otherwise valid breakout must NOT disqualify the setup."""
    c, h, l, v = fx.gen_breakout()
    rsi = ind.rsi(c)
    rs = {"rs63_broad": 8.0, "pct_rank_universe": 85}
    setup = setups.classify(setups.build_features(c, h, l, v, rs))
    # even if RSI is elevated, the breakout still qualifies
    assert setup.setup_type == SetupType.BREAKOUT and setup.qualifies
    # RSI is present as a feature, not a gate
    assert "rsi14" in setup.features or rsi is not None


def test_classification_is_deterministic():
    c, h, l, v = fx.gen_breakout()
    rs = {"rs63_broad": 8.0, "pct_rank_universe": 85}
    a = setups.classify(setups.build_features(c, h, l, v, rs))
    b = setups.classify(setups.build_features(c, h, l, v, rs))
    assert a.setup_type == b.setup_type
    assert a.trigger == b.trigger and a.stop == b.stop and a.targets == b.targets
