"""
Testy renderu PositionPlan po ludzku (investing/formatting.py).

Gwarancje:
  • werdykt po polsku w pierwszej linii (można wchodzić / poczekaj / odpuść / brak danych),
  • ZERO surowych kodów i angielskiego żargonu w tekście (READY_TO_ENTER,
    PULLBACK_CONTINUATION, BULL, „R/R", „Setup", „Trigger"…),
  • READY tłumaczy mechanikę zdaniami (kupuj / linia obrony / cele / ile kupić),
  • DATA INCOMPLETE i NO TRADE są krótkie, bez planu i bez długiego eseju,
  • liczby w formacie polskim („210,69 $", „~15 min"), render nigdy nie rzuca.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from investing.formatting import format_plan
from investing.schemas import (
    PositionPlan, DecisionStatus, SetupType, AssetType, EventPlan, MarketRegime,
)

# kody / żargon, które NIGDY nie powinny trafić do użytkownika
FORBIDDEN = [
    "READY_TO_ENTER", "WAIT_FOR_TRIGGER", "NO_TRADE", "DATA_INCOMPLETE",
    "PULLBACK_CONTINUATION", "WYCKOFF_REVERSAL", "NO_VALID_SETUP",
    "REDUCE_BEFORE_EVENT", "NO_EVENT_RISK", "BULL", "CAUTION",
    "R/R", "heat", "STALE", "Setup:", "Trigger:", "Stop:",
]


def _ready_plan(**over):
    base = dict(
        ticker="NVDA", strategy="POSITION_20_90", horizon_sessions=45,
        decision_status=DecisionStatus.READY_TO_ENTER,
        decision_reason="PULLBACK_CONTINUATION: R/R do T1 = 2.6 ≥ 2.0, reżim BULL.",
        asset_type=AssetType.EQUITY, setup_type=SetupType.PULLBACK_CONTINUATION,
        current_price=210.69, price_delay_seconds=900,
        entry_zone_low=208.0, entry_zone_high=211.0, entry_trigger=211.0,
        max_chase_price=213.0, technical_stop=201.0, thesis_invalidation=199.0,
        risk_per_share=9.69, target_1=232.0, rr_target_1=2.6, target_2=250.0,
        rr_target_2=4.3, risk_budget=500.0, recommended_quantity=51,
        recommended_position_value=10745.0, recommended_portfolio_pct=0.5,
        market_regime=MarketRegime.BULL, macro_impact="Makro sprzyja.",
        sector_rotation="SMH +16% vs rynek (20 dni)", days_to_earnings=65,
        event_plan=EventPlan.NO_EVENT_RISK, data_quality_score=0.92, signal_confidence=0.7,
        bull_case=["NVDA beneficjent capex AI; rekordowe nakłady hyperscalerów."],
        bear_case=["Korekta całego sektora AI po dużych wzrostach."],
    )
    base.update(over)
    return PositionPlan(**base)


def _assert_no_jargon(out):
    for bad in FORBIDDEN:
        assert bad not in out, f"żargon w output: {bad}"


def test_ready_header_and_human_plan():
    out = format_plan(_ready_plan())
    assert out.splitlines()[0] == "✅ *NVDA — można wchodzić*"
    for needle in ("Kupuj", "208 $", "211 $", "Linia obrony", "201 $",
                   "Cele zarobku", "232 $", "razy tyle, czym ryzykujesz",
                   "Ile kupić", "akcji", "Największe ryzyko", "branża"):
        assert needle in out, needle
    _assert_no_jargon(out)


def test_ready_explains_rr_in_words_not_symbol():
    out = format_plan(_ready_plan())
    assert "2,6 razy tyle, czym ryzykujesz" in out
    assert "R/R" not in out


def test_data_incomplete_is_short_and_human():
    out = format_plan(_ready_plan(
        decision_status=DecisionStatus.DATA_INCOMPLETE,
        decision_reason="Nieaktualne wymagane dane: price (STALE), bars (STALE)",
        missing_data=["price (STALE)", "bars (STALE)"], data_quality_score=0.64,
    ))
    assert out.splitlines()[0] == "⚠️ *NVDA — brakuje mi świeżych danych*"
    assert "price" not in out and "bars" not in out
    assert "kurs" in out and "notowania" in out
    assert "sprzed ~15 min" in out
    assert "/wejscie NVDA" in out
    # bez planu i bez długiego eseju
    assert "Kupuj" not in out and "Linia obrony" not in out
    assert "beneficjent capex" not in out
    _assert_no_jargon(out)


def test_no_trade_is_short_without_plan():
    out = format_plan(_ready_plan(
        ticker="BABA", asset_type=AssetType.ADR,
        decision_status=DecisionStatus.NO_TRADE, setup_type=SetupType.NO_VALID_SETUP,
        decision_reason="Brak kwalifikującego się setupu.",
        bear_case=["Ryzyko regulacyjne w Chinach."],
    ))
    assert out.splitlines()[0] == "❌ *BABA — odpuść na teraz*"
    assert "Kupuj" not in out
    assert "Lepiej poczekać" in out
    assert "Główny powód ostrożności" in out
    _assert_no_jargon(out)


def test_wait_is_human():
    out = format_plan(_ready_plan(
        decision_status=DecisionStatus.WAIT_FOR_TRIGGER,
        decision_reason="R/R do T1 = 1.6 < wymagane 2.0 w reżimie BULL.",
    ))
    assert out.splitlines()[0] == "⏳ *NVDA — jeszcze poczekaj*"
    assert "poczekać" in out or "czekać" in out or "poczekaj" in out
    _assert_no_jargon(out)


def test_price_delay_humanised_to_minutes():
    out = format_plan(_ready_plan(
        decision_status=DecisionStatus.DATA_INCOMPLETE,
        missing_data=["price (STALE)"], price_delay_seconds=900,
    ))
    assert "sprzed ~15 min" in out
    assert "900" not in out


def test_render_never_raises_on_minimal_plan():
    plan = PositionPlan(
        ticker="XYZ", strategy="POSITION_20_90", horizon_sessions=45,
        decision_status=DecisionStatus.WAIT_FOR_TRIGGER,
    )
    out = format_plan(plan)
    assert "XYZ" in out
    assert out.splitlines()[0].startswith("⏳ *XYZ")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except Exception as e:  # noqa
            failed += 1; print(f"FAIL {fn.__name__}: {e}")
    sys.exit(1 if failed else 0)
