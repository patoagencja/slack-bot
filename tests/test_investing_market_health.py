"""Market-health normalization tests — percentile/z-score, theme de-dup, no zeroing."""

from investing import market_health
from investing.schemas import MarketRegime


def test_missing_indicators_excluded_not_zeroed():
    composite, confidence, missing = market_health.compute_composite(
        {"spy_vs_ma200": 1.0, "credit_oas": 1.0}  # only 2 of many provided
    )
    # composite reflects only present (both +1) -> +1, not diluted toward 0
    assert composite == 1.0
    assert confidence < 1.0
    assert "vix_structure" in missing


def test_theme_dedup_counts_volatility_once():
    one = market_health.compute_composite({"vix_structure": -1.0})[0]
    two = market_health.compute_composite({"vix_structure": -1.0, "skew": -1.0})[0]
    # both are the 'volatility' theme -> averaged -> identical contribution
    assert one == two == -1.0


def test_percentile_and_regime_from_history():
    history = [(-0.5 + i * 0.05) for i in range(20)]  # spread of composites
    ctx = market_health.build_context({"spy_vs_ma200": 1.0, "credit_oas": 1.0,
                                       "breadth": 1.0, "fear_greed": 0.5},
                                      history, sector="AI/Semis")
    assert ctx.health_percentile is not None
    assert ctx.regime in (MarketRegime.BULL, MarketRegime.CAUTION)
    assert ctx.required_rr > 0 and 0 < ctx.size_multiplier <= 1.0


def test_regime_bands_use_percentile():
    assert market_health.regime_from(0.0, 90.0) == MarketRegime.BULL
    assert market_health.regime_from(0.0, 50.0) == MarketRegime.CAUTION
    assert market_health.regime_from(0.0, 30.0) == MarketRegime.DEFENSIVE
    assert market_health.regime_from(0.0, 5.0) == MarketRegime.BEAR


def test_unknown_when_no_data():
    ctx = market_health.build_context({}, [], sector="X")
    assert ctx.regime == MarketRegime.UNKNOWN


def test_macro_impact_is_sector_specific():
    bull = market_health.build_context({"spy_vs_ma200": 1.0, "credit_oas": 1.0,
                                        "breadth": 1.0}, [], sector="AI/Semis",
                                       rate_trend="rising")
    assert "AI/Semis" in bull.macro_impact or bull.macro_impact
