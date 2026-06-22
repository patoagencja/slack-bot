"""Position-sizing tests — exact formula behavior and the binding constraint."""

from investing import sizing


def test_basic_risk_sizing():
    r = sizing.size_position(entry_price=100.0, stop_price=95.0, portfolio_value=100_000,
                             risk_per_trade_pct=0.5, max_position_pct=0.10,
                             adv_dollars=50_000_000, estimated_slippage=0.0)
    # budget = 100000 * 0.005 = 500 ; risk/share = 5 ; shares_by_risk = 100
    assert r.risk_budget == 500.0
    assert r.risk_per_share == 5.0
    assert r.shares_by_risk == 100
    # position cap = 100000*0.10/100 = 100
    assert r.shares_by_position_cap == 100
    # liquidity = 50M*1%/100 = 5000
    assert r.shares_by_liquidity == 5000
    assert r.final_quantity == 100
    assert r.position_value == 10_000.0
    assert r.portfolio_pct == 10.0


def test_position_cap_binds_when_tighter_than_risk():
    r = sizing.size_position(entry_price=100.0, stop_price=99.0, portfolio_value=100_000,
                             risk_per_trade_pct=2.0, max_position_pct=0.05,
                             adv_dollars=1_000_000_000, estimated_slippage=0.0)
    # risk/share = 1 ; budget = 2000 ; by_risk = 2000 ; cap = 100000*0.05/100 = 50
    assert r.shares_by_risk == 2000
    assert r.shares_by_position_cap == 50
    assert r.final_quantity == 50
    assert r.binding_constraint == "position_cap"


def test_regime_size_multiplier_scales_budget():
    full = sizing.size_position(entry_price=100, stop_price=95, portfolio_value=100_000,
                                risk_per_trade_pct=0.5, max_position_pct=1.0,
                                size_multiplier=1.0, estimated_slippage=0.0)
    half = sizing.size_position(entry_price=100, stop_price=95, portfolio_value=100_000,
                                risk_per_trade_pct=0.5, max_position_pct=1.0,
                                size_multiplier=0.5, estimated_slippage=0.0)
    assert half.shares_by_risk == full.shares_by_risk // 2


def test_invalid_entry_stop_yields_zero():
    r = sizing.size_position(entry_price=100, stop_price=100, portfolio_value=100_000,
                             risk_per_trade_pct=0.5)
    assert r.final_quantity == 0


def test_slippage_increases_risk_per_share():
    r = sizing.size_position(entry_price=100, stop_price=95, portfolio_value=100_000,
                             risk_per_trade_pct=0.5, estimated_slippage=0.5)
    assert r.risk_per_share == 5.5


def test_unknown_liquidity_does_not_expand_size():
    r = sizing.size_position(entry_price=100, stop_price=95, portfolio_value=100_000,
                             risk_per_trade_pct=5.0, max_position_pct=0.10,
                             adv_dollars=None, estimated_slippage=0.0)
    # liquidity falls back to the position cap, never unlimited
    assert r.shares_by_liquidity == r.shares_by_position_cap
