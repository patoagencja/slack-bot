"""
investing/sizing.py — deterministic position sizing.

    risk_budget          = portfolio_value * risk_per_trade * regime_size_multiplier
    risk_per_share       = abs(entry - stop) + estimated_slippage
    shares_by_risk       = floor(risk_budget / risk_per_share)
    shares_by_pos_cap    = floor(portfolio_value * max_position_pct / entry)
    shares_by_liquidity  = floor(adv_dollars * max_adv_participation / entry)
    final_quantity       = min(of the three caps)

The stop is supplied by the setup's *invalidation* logic; ATR only ever acts as a
buffer/sanity-check upstream, never as the sole basis of the stop.
"""

from __future__ import annotations

import math
from typing import Optional

from . import config
from .schemas import SizingResult


def estimate_slippage(entry_price: float, adv_dollars: Optional[float] = None,
                      bps: Optional[float] = None) -> float:
    """Slippage in price units. Defaults to a bps-of-price model; widens for thin
    names when ADV is known."""
    bps = bps if bps is not None else config.SLIPPAGE_BPS_DEFAULT
    if adv_dollars is not None and adv_dollars > 0:
        if adv_dollars < 5_000_000:
            bps *= 3
        elif adv_dollars < 25_000_000:
            bps *= 1.8
    return round(entry_price * bps / 10_000.0, 4)


def size_position(
    *,
    entry_price: float,
    stop_price: float,
    portfolio_value: float,
    risk_per_trade_pct: float,
    max_position_pct: Optional[float] = None,
    adv_dollars: Optional[float] = None,
    size_multiplier: float = 1.0,
    estimated_slippage: Optional[float] = None,
) -> SizingResult:
    res = SizingResult()
    max_position_pct = max_position_pct if max_position_pct is not None else config.MAX_POSITION_PCT

    if entry_price <= 0 or stop_price <= 0 or entry_price <= stop_price:
        res.binding_constraint = "invalid entry/stop"
        return res

    slip = estimated_slippage if estimated_slippage is not None else estimate_slippage(entry_price, adv_dollars)
    res.estimated_slippage = slip
    res.risk_per_share = round(abs(entry_price - stop_price) + slip, 4)
    if res.risk_per_share <= 0:
        res.binding_constraint = "zero risk per share"
        return res

    res.risk_budget = round(portfolio_value * (risk_per_trade_pct / 100.0) * size_multiplier, 2)
    res.shares_by_risk = math.floor(res.risk_budget / res.risk_per_share)
    res.shares_by_position_cap = math.floor(portfolio_value * max_position_pct / entry_price)

    if adv_dollars and adv_dollars > 0:
        res.shares_by_liquidity = math.floor(adv_dollars * config.MAX_ADV_PARTICIPATION / entry_price)
    else:
        # unknown liquidity must not silently expand size — cap at position cap
        res.shares_by_liquidity = res.shares_by_position_cap

    caps = {
        "risk": res.shares_by_risk,
        "position_cap": res.shares_by_position_cap,
        "liquidity": res.shares_by_liquidity,
    }
    res.final_quantity = max(0, min(caps.values()))
    res.binding_constraint = min(caps, key=caps.get)
    res.position_value = round(res.final_quantity * entry_price, 2)
    res.portfolio_pct = round(res.position_value / portfolio_value * 100, 2) if portfolio_value else 0.0
    return res
