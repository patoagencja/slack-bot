"""
investing/portfolio.py — portfolio-level risk checks for a candidate position.

Backed by the positions repository (SQLite). For a new candidate it computes
single-name / sector / narrative concentration, portfolio heat (sum of open risk
to all active stops), portfolio beta, and correlation with existing holdings. If
any hard limit is breached, READY_TO_ENTER is not permitted.
"""

from __future__ import annotations

from typing import Optional, Sequence

from . import config, indicators as ind, persistence
from .schemas import PortfolioImpact


def _position_value(p: dict) -> float:
    return float(p.get("entry_price") or 0) * float(p.get("quantity") or 0)


def _position_risk(p: dict) -> float:
    entry = float(p.get("entry_price") or 0)
    stop = float(p.get("stop_price") or 0)
    qty = float(p.get("quantity") or 0)
    return max(0.0, (entry - stop)) * qty


def correlation(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    ra, rb = ind.daily_returns(a), ind.daily_returns(b)
    n = min(len(ra), len(rb))
    if n < 20:
        return None
    ra, rb = ra[-n:], rb[-n:]
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((ra[i] - ma) * (rb[i] - mb) for i in range(n))
    va = sum((x - ma) ** 2 for x in ra)
    vb = sum((x - mb) ** 2 for x in rb)
    if va <= 0 or vb <= 0:
        return None
    return round(cov / (va ** 0.5 * vb ** 0.5), 3)


def evaluate_new_position(
    *,
    ticker: str,
    sector: str,
    narrative: str,
    entry_price: float,
    stop_price: float,
    quantity: int,
    beta: Optional[float] = None,
    portfolio_value: float,
    open_positions: Optional[list[dict]] = None,
    correlations: Optional[dict[str, float]] = None,
    db_path: Optional[str] = None,
) -> PortfolioImpact:
    if open_positions is None:
        try:
            open_positions = persistence.list_open_positions(db_path)
        except Exception:
            open_positions = []
    correlations = correlations or {}

    pv = max(portfolio_value, 1e-9)
    cand_value = entry_price * quantity
    cand_risk = max(0.0, entry_price - stop_price) * quantity

    sector_val_before = sum(_position_value(p) for p in open_positions
                            if (p.get("sector") or "UNKNOWN") == sector)
    narrative_val = sum(_position_value(p) for p in open_positions
                        if (p.get("narrative") or "UNKNOWN") == narrative)
    heat_before = sum(_position_risk(p) for p in open_positions)

    imp = PortfolioImpact(
        sector=sector,
        narrative=narrative,
        sector_exposure_before=round(sector_val_before / pv, 4),
        sector_exposure_after=round((sector_val_before + cand_value) / pv, 4),
        narrative_exposure_after=round((narrative_val + cand_value) / pv, 4),
        single_name_pct_after=round(cand_value / pv, 4),
        heat_before=round(heat_before / pv, 4),
        heat_after=round((heat_before + cand_risk) / pv, 4),
    )

    # portfolio beta (value-weighted) after adding candidate
    if beta is not None:
        tot_val = sum(_position_value(p) for p in open_positions) + cand_value
        if tot_val > 0:
            bsum = sum((p.get("beta") or 1.0) * _position_value(p) for p in open_positions)
            bsum += beta * cand_value
            imp.portfolio_beta_after = round(bsum / tot_val, 3)

    # correlation warnings
    high = [(t, c) for t, c in correlations.items() if c is not None and c >= config.CORRELATION_WARN]
    if high:
        worst = max(high, key=lambda x: x[1])
        imp.correlation_warning = (
            f"Wysoka korelacja z {worst[0]} ({worst[1]:.2f} ≥ {config.CORRELATION_WARN})"
        )

    # hard-limit breaches
    if imp.single_name_pct_after > config.MAX_POSITION_PCT:
        imp.limit_breaches.append(
            f"single-name {imp.single_name_pct_after:.0%} > {config.MAX_POSITION_PCT:.0%}")
    if imp.sector_exposure_after > config.MAX_SECTOR_PCT:
        imp.limit_breaches.append(
            f"sektor {sector} {imp.sector_exposure_after:.0%} > {config.MAX_SECTOR_PCT:.0%}")
    if imp.narrative_exposure_after > config.MAX_NARRATIVE_PCT:
        imp.limit_breaches.append(
            f"narracja {narrative} {imp.narrative_exposure_after:.0%} > {config.MAX_NARRATIVE_PCT:.0%}")
    if imp.heat_after > config.MAX_PORTFOLIO_HEAT_PCT:
        imp.limit_breaches.append(
            f"portfolio heat {imp.heat_after:.1%} > {config.MAX_PORTFOLIO_HEAT_PCT:.1%}")
    return imp
