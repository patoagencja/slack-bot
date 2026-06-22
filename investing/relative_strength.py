"""
investing/relative_strength.py — multi-window, risk-adjusted relative strength.

Replaces the old "30d return minus QQQ" single number (kept only as one auxiliary
feature). Computes RS21/63/126 vs a broad benchmark and a sector benchmark, plus
beta-adjusted excess return, volatility-adjusted RS, and percentile ranks within
the sector and the whole universe.
"""

from __future__ import annotations

from typing import Optional, Sequence

from . import indicators as ind


def _ret(closes: Sequence[float], n: int) -> Optional[float]:
    return ind.pct_return(closes, n)


def beta(stock_closes: Sequence[float], bench_closes: Sequence[float],
         window: int = 126) -> Optional[float]:
    sr = ind.daily_returns(stock_closes[-window - 1:])
    br = ind.daily_returns(bench_closes[-window - 1:])
    n = min(len(sr), len(br))
    if n < 20:
        return None
    sr, br = sr[-n:], br[-n:]
    mb = sum(br) / n
    ms = sum(sr) / n
    cov = sum((br[i] - mb) * (sr[i] - ms) for i in range(n)) / (n - 1)
    var = sum((b - mb) ** 2 for b in br) / (n - 1)
    return cov / var if var else None


def compute(
    stock_closes: Sequence[float],
    broad_closes: Sequence[float],
    sector_closes: Optional[Sequence[float]] = None,
) -> dict:
    """Return a dict of RS features. Any input too short yields None for that field
    (never silently 0)."""
    out: dict[str, Optional[float]] = {}
    for w in (21, 63, 126):
        s = _ret(stock_closes, w)
        b = _ret(broad_closes, w)
        out[f"rs{w}_broad"] = round(s - b, 2) if (s is not None and b is not None) else None
        if sector_closes is not None:
            sec = _ret(sector_closes, w)
            out[f"rs{w}_sector"] = round(s - sec, 2) if (s is not None and sec is not None) else None
        else:
            out[f"rs{w}_sector"] = None

    bt = beta(stock_closes, broad_closes)
    out["beta"] = round(bt, 3) if bt is not None else None
    s63 = _ret(stock_closes, 63)
    b63 = _ret(broad_closes, 63)
    if s63 is not None and b63 is not None and bt is not None:
        out["beta_adj_excess_63"] = round(s63 - bt * b63, 2)
    else:
        out["beta_adj_excess_63"] = None

    vol = ind.stdev(ind.daily_returns(stock_closes[-64:]))
    if out.get("rs63_broad") is not None and vol:
        out["vol_adj_rs_63"] = round(out["rs63_broad"] / (vol * 100), 3)
    else:
        out["vol_adj_rs_63"] = None

    # auxiliary legacy feature, explicitly demoted
    out["aux_30d_minus_broad"] = (
        round(_ret(stock_closes, 30) - _ret(broad_closes, 30), 2)
        if (_ret(stock_closes, 30) is not None and _ret(broad_closes, 30) is not None)
        else None
    )
    return out


def percentile_rank(value: float, population: Sequence[float]) -> Optional[float]:
    """Percentile (0-100) of ``value`` within ``population`` (inclusive)."""
    pop = [p for p in population if p is not None]
    if not pop:
        return None
    below = sum(1 for p in pop if p < value)
    equal = sum(1 for p in pop if p == value)
    return round((below + 0.5 * equal) / len(pop) * 100, 1)


def add_percentile_ranks(rs: dict, *, sector_population: Sequence[float],
                         universe_population: Sequence[float],
                         key: str = "rs63_broad") -> dict:
    """Attach sector- and universe-percentile ranks for the given RS metric."""
    v = rs.get(key)
    rs["pct_rank_sector"] = percentile_rank(v, sector_population) if v is not None else None
    rs["pct_rank_universe"] = percentile_rank(v, universe_population) if v is not None else None
    return rs
