"""
investing/formatting.py — Slack rendering of a PositionPlan.

First line is ALWAYS the decision status. Then strategy, horizon, entry zone,
trigger, max chase, invalidation, stop, targets, R/R, sizing, event risk,
portfolio impact, data quality and the single most important reason. The point of
the rebuild: hand the user a trade to execute, not a company description.
"""

from __future__ import annotations

from .schemas import DecisionStatus, PositionPlan

_STATUS_LINE = {
    DecisionStatus.READY_TO_ENTER: "🟢 READY TO ENTER",
    DecisionStatus.WAIT_FOR_TRIGGER: "🟡 WAIT FOR TRIGGER",
    DecisionStatus.NO_TRADE: "🔴 NO TRADE",
    DecisionStatus.DATA_INCOMPLETE: "⚪ DATA INCOMPLETE",
}


def _f(v, prefix="$", suffix="", nd=2):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{prefix}{v:.{nd}f}{suffix}"
    return f"{prefix}{v}{suffix}"


def format_plan(plan: PositionPlan) -> str:
    lines: list[str] = []
    lines.append(f"*{_STATUS_LINE.get(plan.decision_status, plan.decision_status.value)}* — "
                 f"*{plan.ticker}* ({plan.asset_type.value})")
    lines.append(f"_{plan.decision_reason}_")
    lines.append("")
    lines.append(f"• *Strategia:* {plan.strategy} | *Setup:* {plan.setup_type.value} | "
                 f"*Horyzont:* {plan.horizon_sessions} sesji")
    lines.append(f"• *Cena:* {_f(plan.current_price)} "
                 f"(opóźnienie ~{int(plan.price_delay_seconds or 0)}s)")

    if plan.decision_status in (DecisionStatus.READY_TO_ENTER, DecisionStatus.WAIT_FOR_TRIGGER):
        lines.append(f"• *Strefa wejścia:* {_f(plan.entry_zone_low)}–{_f(plan.entry_zone_high)} | "
                     f"*Trigger:* {_f(plan.entry_trigger)} | *Max chase:* {_f(plan.max_chase_price)}")
        lines.append(f"• *Stop (techniczny):* {_f(plan.technical_stop)} | "
                     f"*Unieważnienie tezy:* {_f(plan.thesis_invalidation)} | "
                     f"*Ryzyko/akcję:* {_f(plan.risk_per_share)} (slippage {_f(plan.estimated_slippage)})")
        lines.append(f"• *Targety:* T1 {_f(plan.target_1)} (R/R {plan.rr_target_1}) | "
                     f"T2 {_f(plan.target_2)} (R/R {plan.rr_target_2}) | "
                     f"T3 {_f(plan.target_3)} (R/R {plan.rr_target_3})")
        lines.append(f"• *Sizing:* {plan.recommended_quantity} szt. "
                     f"({_f(plan.recommended_position_value)}, {plan.recommended_portfolio_pct}% portfela) | "
                     f"*Budżet ryzyka:* {_f(plan.risk_budget)}")

    # event risk
    er = f"{plan.event_plan.value}"
    if plan.days_to_earnings is not None:
        er += f" | earnings za {plan.days_to_earnings} dni"
    lines.append(f"• *Event risk:* {er}")

    # portfolio + macro
    lines.append(f"• *Portfel:* sektor {(_pct(plan.portfolio_sector_exposure_before))}→"
                 f"{_pct(plan.portfolio_sector_exposure_after)}, "
                 f"heat {_pct(plan.portfolio_heat_before)}→{_pct(plan.portfolio_heat_after)}"
                 + (f" ⚠️ {plan.correlation_warning}" if plan.correlation_warning else ""))
    lines.append(f"• *Reżim:* {plan.market_regime.value} | {plan.macro_impact}")
    if plan.sector_rotation:
        lines.append(f"• *Rotacja sektora:* {plan.sector_rotation}")

    # data quality
    dq = f"{plan.data_quality_score:.0%} | pewność sygnału {plan.signal_confidence:.0%}"
    if plan.missing_data:
        dq += f" | brakuje: {', '.join(plan.missing_data)}"
    lines.append(f"• *Data quality:* {dq}")

    if plan.bull_case:
        lines.append(f"• *Bull:* {'; '.join(plan.bull_case[:3])}")
    if plan.bear_case:
        lines.append(f"• *Bear:* {'; '.join(plan.bear_case[:3])}")
    if plan.conditions_to_cancel:
        lines.append(f"• *Anuluj jeśli:* {'; '.join(plan.conditions_to_cancel[:3])}")

    return "\n".join(lines)


def _pct(v):
    return "—" if v is None else f"{v:.0%}"
