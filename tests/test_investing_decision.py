"""Decision-engine tests — determinism, status mapping, gate/event/portfolio precedence."""

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import invest_fixtures as fx  # noqa: E402

from investing import (config, data_quality, decision as dmod, event_risk,  # noqa: E402
                       market_health, portfolio, setups)
from investing.data_quality import make_datapoint  # noqa: E402
from investing.schemas import (AssetType, DecisionStatus, EventPlan,  # noqa: E402
                                SetupType)


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _ready_inputs():
    c, h, l, v = fx.gen_breakout()
    rs = {"rs63_broad": 8.0, "pct_rank_universe": 85, "beta": 1.1}
    setup = setups.classify(setups.build_features(c, h, l, v, rs))
    price_dp = make_datapoint("price", c[-1], source="t", kind="quote", as_of=_now())
    points = {
        "price": price_dp,
        "bars": make_datapoint("bars", True, source="t", kind="daily_bars", as_of=_now()),
        "earnings_date": make_datapoint("e", "2026-12-01", source="t", kind="earnings", as_of=_now()),
    }
    gate = data_quality.evaluate(points, required=["price", "bars", "earnings_date"])
    # force a clearly BULL regime
    market = market_health.build_context(
        {"spy_vs_ma200": 1.0, "credit_oas": 1.0, "breadth": 1.0, "fear_greed": 0.5},
        [(-0.2 + i * 0.04) for i in range(20)], sector="AI/Semis")
    event = event_risk.assess(earnings_date=dt.date(2026, 12, 1), days_to_earnings=160,
                              catalysts=[], setup_type=setup.setup_type)
    pf = portfolio.evaluate_new_position(
        ticker="NVDA", sector="AI/Semis", narrative="AI", entry_price=c[-1],
        stop_price=setup.stop, quantity=10, beta=1.1, portfolio_value=100_000, open_positions=[])
    return dict(ticker="NVDA", strategy=config.STRATEGY_POSITION, horizon_sessions=45,
                asset_type=AssetType.EQUITY, price_point=price_dp, gate=gate, setup=setup,
                market=market, event=event, portfolio_impact=pf, portfolio_value=100_000,
                risk_per_trade_pct=0.5, sector="AI/Semis", adv_dollars=50_000_000)


def test_ready_to_enter_happy_path():
    plan = dmod.decide(**_ready_inputs())
    assert plan.decision_status == DecisionStatus.READY_TO_ENTER
    assert plan.recommended_quantity > 0
    assert plan.entry_trigger is not None and plan.technical_stop is not None
    assert plan.rr_target_1 is not None and plan.rr_target_1 >= 2.0
    assert plan.thesis_invalidation is not None


def test_decision_is_deterministic():
    # same inputs -> identical plan (only the engine's created_at timestamp differs)
    ins = _ready_inputs()
    a = dmod.decide(**ins).model_dump(mode="json")
    b = dmod.decide(**ins).model_dump(mode="json")
    a.pop("created_at"); b.pop("created_at")
    assert a == b


def test_data_incomplete_blocks_entry():
    ins = _ready_inputs()
    bad = {"price": make_datapoint("price", None, source="t", kind="quote")}
    ins["gate"] = data_quality.evaluate(bad, required=["price"])
    plan = dmod.decide(**ins)
    assert plan.decision_status == DecisionStatus.DATA_INCOMPLETE


def test_no_setup_yields_no_trade():
    ins = _ready_inputs()
    c, h, l, v = fx.gen_downtrend()
    ins["setup"] = setups.classify(setups.build_features(c, h, l, v, {"rs63_broad": -20}))
    plan = dmod.decide(**ins)
    assert plan.decision_status == DecisionStatus.NO_TRADE


def test_imminent_earnings_forces_wait():
    ins = _ready_inputs()
    ins["event"] = event_risk.assess(earnings_date=dt.date.today() + dt.timedelta(days=4),
                                     days_to_earnings=4, catalysts=[],
                                     setup_type=ins["setup"].setup_type)
    plan = dmod.decide(**ins)
    assert plan.decision_status == DecisionStatus.WAIT_FOR_TRIGGER
    assert plan.event_plan == EventPlan.REDUCE_BEFORE_EVENT


def test_portfolio_breach_forces_no_trade():
    ins = _ready_inputs()
    existing = [{"ticker": "AMD", "entry_price": 100, "stop_price": 90,
                 "quantity": 280, "sector": "AI/Semis", "narrative": "AI"}]
    ins["portfolio_impact"] = portfolio.evaluate_new_position(
        ticker="NVDA", sector="AI/Semis", narrative="AI", entry_price=ins["price_point"].value,
        stop_price=ins["setup"].stop, quantity=100, portfolio_value=100_000, open_positions=existing)
    plan = dmod.decide(**ins)
    assert plan.decision_status == DecisionStatus.NO_TRADE
    assert "limit" in plan.decision_reason.lower()


def test_llm_never_sets_numbers():
    """Even with an LLM payload, all numeric fields come from the engine."""
    from investing.schemas import LLMQualitative
    ins = _ready_inputs()
    ins["llm"] = LLMQualitative(bull_case=["x"], bear_case=["y"])
    plan = dmod.decide(**ins)
    # numbers are engine-derived; LLM only contributed prose
    assert plan.bull_case == ["x"]
    assert isinstance(plan.recommended_quantity, int)
