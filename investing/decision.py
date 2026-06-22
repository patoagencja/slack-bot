"""
investing/decision.py — the deterministic, audited decision engine (P0.3).

Given the data-quality gate, the classified setup, market context, event risk,
portfolio impact and (optionally) the LLM's *qualitative* notes, it computes a
single :class:`PositionPlan`. Every number — status, entry, stop, targets, R/R,
quantity — is produced by code here. The LLM only contributes prose (bull/bear,
cancel/recheck conditions). The mapping is pure: same inputs -> same plan.
"""

from __future__ import annotations

from typing import Optional

from . import config, sizing as sizing_mod
from .data_quality import GateResult
from .schemas import (DecisionStatus, EventPlan, EventRiskAssessment, LLMQualitative,
                      MarketContext, PortfolioImpact, PositionPlan, SetupClassification,
                      SetupType, AssetType, DataPoint)


def _rr(entry: float, stop: float, target: Optional[float]) -> Optional[float]:
    if target is None or entry <= stop:
        return None
    return round((target - entry) / (entry - stop), 2)


def _entry_timing(setup: SetupClassification, price: float) -> tuple[str, float]:
    """Return ('ready'|'wait', entry_price). For a wait, the planned entry is the
    trigger; for ready it is the current price (clamped into the zone)."""
    zone = setup.entry_zone
    trig = setup.trigger
    st = setup.setup_type

    if st == SetupType.BREAKOUT and zone and trig is not None:
        low, high = zone
        if low <= price <= high:
            return "ready", price
        return "wait", trig                       # below pivot, or too extended (chase)
    if st == SetupType.PULLBACK_CONTINUATION and trig is not None:
        if price >= trig:
            return "ready", price
        return "wait", trig
    if st in (SetupType.MEAN_REVERSION, SetupType.WYCKOFF_REVERSAL):
        return ("ready", price) if setup.qualifies else ("wait", trig or price)
    if st == SetupType.BASE_BUILDING:
        return "wait", trig or price
    if st == SetupType.EVENT_DRIVEN:
        return "wait", trig or price
    return "wait", trig or price


def decide(
    *,
    ticker: str,
    strategy: str,
    horizon_sessions: int,
    asset_type: AssetType,
    price_point: DataPoint,
    gate: GateResult,
    setup: SetupClassification,
    market: MarketContext,
    event: EventRiskAssessment,
    portfolio_impact: PortfolioImpact,
    portfolio_value: float,
    risk_per_trade_pct: float,
    sector: str = "UNKNOWN",
    adv_dollars: Optional[float] = None,
    max_position_pct: Optional[float] = None,
    llm: Optional[LLMQualitative] = None,
    rs: Optional[dict] = None,
    feature_snapshot: Optional[dict] = None,
) -> PositionPlan:
    plan = PositionPlan(
        ticker=ticker,
        asset_type=asset_type,
        strategy=strategy,
        horizon_sessions=horizon_sessions,
        decision_status=DecisionStatus.NO_TRADE,
        setup_type=setup.setup_type,
        market_regime=market.regime,
        macro_impact=market.macro_impact,
        sector_rotation=market.sector_rotation,
        data_quality_score=round(gate.score, 3),
        missing_data=list(gate.missing) + [f"{f} (STALE)" for f in gate.stale],
        config_version=config.CONFIG_VERSION,
        code_version=config.code_version(),
        model_version=config.model_version(),
        feature_snapshot=feature_snapshot or {},
        current_price=price_point.value,
        price_as_of=price_point.as_of,
        price_delay_seconds=price_point.age_seconds,
        earnings_date=event.earnings_date,
        days_to_earnings=event.days_to_earnings,
        event_risk=event.has_binary_event,
        event_plan=event.event_plan,
        portfolio_sector_exposure_before=portfolio_impact.sector_exposure_before,
        portfolio_sector_exposure_after=portfolio_impact.sector_exposure_after,
        portfolio_heat_before=portfolio_impact.heat_before,
        portfolio_heat_after=portfolio_impact.heat_after,
        correlation_warning=portfolio_impact.correlation_warning,
    )

    # qualitative prose (LLM never touches numbers)
    if llm:
        plan.bull_case = llm.bull_case
        plan.bear_case = llm.bear_case
        plan.conditions_to_cancel = list(setup.cancel_conditions) + list(llm.thesis_invalidation_qualitative)
        plan.conditions_to_recheck = list(setup.recheck_conditions)
    else:
        plan.conditions_to_cancel = list(setup.cancel_conditions)
        plan.conditions_to_recheck = list(setup.recheck_conditions)

    # ── 1. data-quality gate ──
    if not gate.can_enter:
        plan.decision_status = DecisionStatus.DATA_INCOMPLETE
        plan.decision_reason = "; ".join(gate.reasons) or "Niekompletne / nieaktualne dane"
        plan.signal_confidence = round(setup.score / 100.0 * gate.score, 3)
        return plan

    # ── 2. no valid setup ──
    if setup.setup_type == SetupType.NO_VALID_SETUP or not setup.qualifies:
        plan.decision_status = DecisionStatus.NO_TRADE
        plan.decision_reason = "Brak kwalifikującego się setupu: " + "; ".join(setup.reasons[:2])
        plan.signal_confidence = round(setup.score / 100.0 * gate.score, 3)
        return plan

    price = price_point.value
    if price is None or setup.stop is None or setup.stop <= 0:
        plan.decision_status = DecisionStatus.DATA_INCOMPLETE
        plan.decision_reason = "Brak ceny lub poziomu stopa do zbudowania planu"
        return plan

    timing, entry_price = _entry_timing(setup, price)

    # ── stop / invalidation / sizing ──
    technical_stop = setup.stop
    structural = (setup.features.get("base_low")
                  or setup.features.get("support")
                  or setup.features.get("ma50")
                  or technical_stop)
    thesis_invalidation = min(technical_stop, structural) if structural else technical_stop

    sz = sizing_mod.size_position(
        entry_price=entry_price,
        stop_price=technical_stop,
        portfolio_value=portfolio_value,
        risk_per_trade_pct=risk_per_trade_pct,
        max_position_pct=max_position_pct,
        adv_dollars=adv_dollars,
        size_multiplier=market.size_multiplier,
    )

    targets = (setup.targets + [None, None, None])[:3]
    rr1 = _rr(entry_price, technical_stop, targets[0])
    rr2 = _rr(entry_price, technical_stop, targets[1])
    rr3 = _rr(entry_price, technical_stop, targets[2])

    plan.entry_zone_low = setup.entry_zone[0] if setup.entry_zone else None
    plan.entry_zone_high = setup.entry_zone[1] if setup.entry_zone else None
    plan.entry_trigger = setup.trigger
    plan.max_chase_price = setup.max_chase
    plan.technical_stop = round(technical_stop, 2)
    plan.thesis_invalidation = round(thesis_invalidation, 2)
    plan.estimated_slippage = sz.estimated_slippage
    plan.risk_per_share = sz.risk_per_share
    plan.target_1, plan.target_2, plan.target_3 = targets
    plan.rr_target_1, plan.rr_target_2, plan.rr_target_3 = rr1, rr2, rr3
    plan.risk_budget = sz.risk_budget
    plan.recommended_quantity = sz.final_quantity
    plan.recommended_position_value = sz.position_value
    plan.recommended_portfolio_pct = sz.portfolio_pct
    plan.signal_confidence = round(min(1.0, setup.score / 100.0) * gate.score, 3)

    required_rr = market.required_rr

    # ── decision precedence ──
    if portfolio_impact.limit_breaches:
        plan.decision_status = DecisionStatus.NO_TRADE
        plan.decision_reason = "Przekroczone limity ryzyka portfela: " + "; ".join(portfolio_impact.limit_breaches)
        return plan

    if sz.final_quantity <= 0:
        plan.decision_status = DecisionStatus.NO_TRADE
        plan.decision_reason = (
            f"Nie da się zbudować pozycji w budżecie ryzyka "
            f"(risk/share ${sz.risk_per_share}, budżet ${sz.risk_budget})"
        )
        return plan

    if rr1 is not None and rr1 < required_rr:
        plan.decision_status = DecisionStatus.WAIT_FOR_TRIGGER
        plan.decision_reason = (
            f"R/R do T1 = {rr1} < wymagane {required_rr} w reżimie {market.regime.value} — "
            "czekaj na lepsze wejście (niżej w strefie)."
        )
        return plan

    if event.blocks_full_entry:
        plan.decision_status = DecisionStatus.WAIT_FOR_TRIGGER
        plan.decision_reason = (
            f"Wydarzenie binarne za {event.days_to_earnings} dni — pełne wejście zablokowane "
            f"({event.event_plan.value}). Wejdź po wydarzeniu lub jako starter."
        )
        return plan

    if timing == "wait":
        plan.decision_status = DecisionStatus.WAIT_FOR_TRIGGER
        plan.decision_reason = (
            f"Setup {setup.setup_type.value} ważny, ale trigger nie aktywowany "
            f"(cena {price} vs trigger {setup.trigger}). " + (setup.reasons[0] if setup.reasons else "")
        )
        return plan

    # all gates passed
    plan.decision_status = DecisionStatus.READY_TO_ENTER
    plan.decision_reason = (
        f"{setup.setup_type.value}: cena w strefie wejścia, R/R do T1 = {rr1} ≥ {required_rr}, "
        f"size {sz.final_quantity} szt. (limit: {sz.binding_constraint}), reżim {market.regime.value}."
    )
    return plan
