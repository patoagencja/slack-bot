"""
investing/backtest.py — recommendation outcome tracking & shadow mode.

For each stored recommendation we can, after 5/10/20/40/60 sessions, compute MFE,
MAE, realized R, whether stop / T1 / T2 were hit, time-to-target, gap risk and max
drawdown — and break results down by setup / sector / regime. A shadow mode lets
the old and new systems produce decisions in parallel for comparison, with no
execution.
"""

from __future__ import annotations

from typing import Optional, Sequence

from . import persistence

HORIZONS = (5, 10, 20, 40, 60)


def compute_outcome(entry: float, stop: float, targets: Sequence[float],
                    bars: Sequence[dict]) -> dict:
    """Compute outcome stats over ``bars`` (each {high, low, close}) after entry.

    R is measured in units of initial risk (entry - stop). Stop/target precedence
    within a bar is conservative: if a bar's low <= stop we assume the stop filled
    that session (worst-case for longs)."""
    risk = entry - stop
    if risk <= 0 or not bars:
        return {"valid": False}

    t1 = targets[0] if len(targets) > 0 else None
    t2 = targets[1] if len(targets) > 1 else None

    mfe = mae = 0.0
    hit_stop = hit_t1 = hit_t2 = False
    time_to_t1: Optional[int] = None
    realized_r: Optional[float] = None
    max_dd = 0.0
    prev_close = entry
    max_gap = 0.0

    for i, b in enumerate(bars):
        hi, lo, cl = b["high"], b["low"], b["close"]
        mfe = max(mfe, (hi - entry) / risk)
        mae = min(mae, (lo - entry) / risk)
        max_dd = min(max_dd, (lo - entry) / risk)
        max_gap = max(max_gap, abs(b.get("open", cl) - prev_close) / entry)
        prev_close = cl

        if not (hit_stop or hit_t1) and lo <= stop:
            hit_stop = True
            realized_r = -1.0          # stopped out
            break
        if t1 is not None and hi >= t1 and not hit_t1:
            hit_t1 = True
            time_to_t1 = i + 1
        if t2 is not None and hi >= t2:
            hit_t2 = True

    if realized_r is None:
        realized_r = (bars[-1]["close"] - entry) / risk

    return {
        "valid": True,
        "mfe": round(mfe, 3),
        "mae": round(mae, 3),
        "r_multiple": round(realized_r, 3),
        "hit_stop": hit_stop,
        "hit_target_1": hit_t1,
        "hit_target_2": hit_t2,
        "time_to_target": time_to_t1,
        "gap_risk": round(max_gap, 4),
        "max_drawdown": round(max_dd, 3),
    }


def record_outcomes_at_horizons(plan_id: int, ticker: str, entry: float, stop: float,
                                targets: Sequence[float], bars: Sequence[dict],
                                *, setup_type: str = "", sector: str = "",
                                market_regime: str = "", db_path: Optional[str] = None) -> list[int]:
    """Compute and persist outcome rows for each horizon that ``bars`` covers."""
    ids = []
    for h in HORIZONS:
        if len(bars) < h:
            continue
        out = compute_outcome(entry, stop, targets, bars[:h])
        if not out.get("valid"):
            continue
        out.update({"plan_id": plan_id, "ticker": ticker, "horizon_session": h,
                    "as_of": None, "price": bars[h - 1]["close"],
                    "setup_type": setup_type, "sector": sector, "market_regime": market_regime})
        ids.append(persistence.save_outcome(out, db_path=db_path))
    return ids


def shadow_record(*, ticker: str, old_decision: str, new_status: str,
                  new_setup: str, db_path: Optional[str] = None) -> int:
    """Record old-vs-new decisions in parallel (no execution) for comparison."""
    return persistence.log_signal(
        ticker, "SHADOW", new_setup, 0.0, 0.0,
        {"old_decision": old_decision, "new_status": new_status}, db_path=db_path,
    )
