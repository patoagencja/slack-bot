"""
Testy czytelnego renderu PositionPlan (investing/formatting.py).

Gwarantują, że:
  • output zaczyna się od statusu decyzji,
  • surowe kody enumów NIE wyciekają do użytkownika (PULLBACK_CONTINUATION, BULL,
    REDUCE_BEFORE_EVENT, "price, bars") — są tłumaczone na polski,
  • READY/WAIT pokazują blok planu (strefa/stop/targety/pozycja),
  • DATA INCOMPLETE / NO TRADE są krótkie i bez planu,
  • render nigdy nie rzuca wyjątkiem.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from investing.formatting import format_plan
from investing.schemas import (
    PositionPlan, DecisionStatus, SetupType, AssetType, EventPlan, MarketRegime,
)


def _ready_plan(**over):
    base = dict(
        ticker="NVDA", strategy="POSITION_20_90", horizon_sessions=45,
        decision_status=DecisionStatus.READY_TO_ENTER,
        decision_reason="Cofnięcie do MA50 w mocnym trendzie — wchodzić w strefie.",
        asset_type=AssetType.EQUITY, setup_type=SetupType.PULLBACK_CONTINUATION,
        current_price=210.69, price_delay_seconds=900,
        entry_zone_low=208.0, entry_zone_high=211.0, entry_trigger=211.0,
        max_chase_price=213.0, technical_stop=201.0, thesis_invalidation=199.0,
        risk_per_share=9.69, target_1=232.0, rr_target_1=2.6, target_2=250.0,
        rr_target_2=4.3, risk_budget=500.0, recommended_quantity=51,
        recommended_position_value=10745.0, recommended_portfolio_pct=0.5,
        market_regime=MarketRegime.BULL, macro_impact="Makro sprzyja — korekty to okazje.",
        sector_rotation="SMH +16% vs rynek (20 dni)",
        days_to_earnings=65, event_plan=EventPlan.NO_EVENT_RISK,
        data_quality_score=0.92, signal_confidence=0.7,
        bull_case=["Beneficjent capex AI", "Rekordowe nakłady hyperscalerów"],
        bear_case=["Korekta sektora po rajdzie AI"],
    )
    base.update(over)
    return PositionPlan(**base)


def test_starts_with_status_and_ticker():
    out = format_plan(_ready_plan())
    assert out.splitlines()[0].startswith("*🟢 READY TO ENTER*")
    assert "NVDA" in out.splitlines()[0]


def test_ready_shows_plan_block_with_numbers():
    out = format_plan(_ready_plan())
    for needle in ("Plan wejścia", "Strefa wejścia", "208", "211",
                   "Stop:", "201", "Targety:", "232", "R/R 2.6",
                   "Pozycja:", "Nie gonić powyżej"):
        assert needle in out, needle


def test_no_raw_enum_codes_leak():
    out = format_plan(_ready_plan())
    for code in ("PULLBACK_CONTINUATION", "REDUCE_BEFORE_EVENT", "NO_EVENT_RISK"):
        assert code not in out, code
    # regime "BULL" must be humanised; ensure the bare token isn't shown as a label
    assert "*Reżim:* 🟢 hossa" in out
    assert "cofnięcie w trendzie wzrostowym" in out


def test_price_delay_is_humanised_to_minutes():
    out = format_plan(_ready_plan(price_delay_seconds=900))
    assert "~15 min" in out
    assert "900s" not in out


def test_data_incomplete_is_short_and_humanised():
    plan = _ready_plan(
        decision_status=DecisionStatus.DATA_INCOMPLETE,
        decision_reason="Nieaktualne wymagane dane: price, bars",
        missing_data=["price", "bars"],
        data_quality_score=0.64,
    )
    out = format_plan(plan)
    assert out.splitlines()[0].startswith("*⚪ DATA INCOMPLETE*")
    # raw field names translated
    assert "price, bars" not in out
    assert "świece (dane cenowe)" in out
    assert "kurs" in out
    # no entry plan, no long bull essay
    assert "Plan wejścia" not in out
    assert "Beneficjent capex AI" not in out
    assert "/wejscie NVDA" in out


def test_no_trade_is_short_without_plan():
    plan = _ready_plan(
        decision_status=DecisionStatus.NO_TRADE,
        setup_type=SetupType.NO_VALID_SETUP,
        decision_reason="Brak ważnego setupu w obecnym układzie.",
    )
    out = format_plan(plan)
    assert out.splitlines()[0].startswith("*🔴 NO TRADE*")
    assert "Plan wejścia" not in out
    assert "Dlaczego nie teraz" in out


def test_render_never_raises_on_minimal_plan():
    plan = PositionPlan(
        ticker="XYZ", strategy="POSITION_20_90", horizon_sessions=45,
        decision_status=DecisionStatus.WAIT_FOR_TRIGGER,
    )
    out = format_plan(plan)
    assert "XYZ" in out
    assert out.splitlines()[0].startswith("*🟡 WAIT FOR TRIGGER*")


if __name__ == "__main__":
    # Standalone runner (działa też bez pytest)
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except Exception as e:  # noqa
            failed += 1; print(f"FAIL {fn.__name__}: {e}")
    # pokaż przykładowy output
    print("\n--- PRZYKŁAD: READY ---\n" + format_plan(_ready_plan()))
    sys.exit(1 if failed else 0)
