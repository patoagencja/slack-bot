"""Portfolio-risk tests — concentration, heat, correlation."""

from investing import config, portfolio


def test_single_name_concentration_breach():
    imp = portfolio.evaluate_new_position(
        ticker="NVDA", sector="AI/Semis", narrative="AI",
        entry_price=100, stop_price=95, quantity=200,  # 20k = 20% of 100k
        portfolio_value=100_000, open_positions=[],
    )
    assert any("single-name" in b for b in imp.limit_breaches)


def test_sector_concentration_breach():
    existing = [{"ticker": "AMD", "entry_price": 100, "stop_price": 90,
                 "quantity": 250, "sector": "AI/Semis", "narrative": "AI"}]  # 25%
    imp = portfolio.evaluate_new_position(
        ticker="NVDA", sector="AI/Semis", narrative="AI",
        entry_price=100, stop_price=95, quantity=90,  # +9% -> 34% sector
        portfolio_value=100_000, open_positions=existing,
    )
    assert any("sektor" in b for b in imp.limit_breaches)


def test_portfolio_heat_accumulates():
    existing = [{"ticker": "AMD", "entry_price": 100, "stop_price": 90,
                 "quantity": 50, "sector": "AI/Semis", "narrative": "AI"}]  # risk 500
    imp = portfolio.evaluate_new_position(
        ticker="NVDA", sector="Tech/Cloud", narrative="AI",
        entry_price=100, stop_price=95, quantity=50,  # risk 250
        portfolio_value=100_000, open_positions=existing,
    )
    assert imp.heat_before == 0.005     # 500/100000
    assert round(imp.heat_after, 4) == 0.0075


def test_correlation_warning():
    imp = portfolio.evaluate_new_position(
        ticker="NVDA", sector="AI/Semis", narrative="AI",
        entry_price=100, stop_price=95, quantity=10,
        portfolio_value=100_000, open_positions=[],
        correlations={"AMD": 0.92},
    )
    assert imp.correlation_warning is not None
    assert "AMD" in imp.correlation_warning


def test_clean_position_no_breach():
    imp = portfolio.evaluate_new_position(
        ticker="NVDA", sector="AI/Semis", narrative="AI",
        entry_price=100, stop_price=95, quantity=10,  # 1%
        portfolio_value=100_000, open_positions=[],
    )
    assert imp.limit_breaches == []
