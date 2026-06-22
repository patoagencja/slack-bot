"""
investing/indicators.py — pure-Python technical indicators.

No pandas / numpy dependency on purpose: the decision core must import and run
(and be unit-tested) without the heavy data stack. Inputs are plain lists of
floats, oldest-first.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence


def sma(values: Sequence[float], n: int) -> Optional[float]:
    if len(values) < n or n <= 0:
        return None
    return sum(values[-n:]) / n


def ema_series(values: Sequence[float], n: int) -> list[float]:
    if not values or n <= 0:
        return []
    k = 2 / (n + 1)
    out = [float(values[0])]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(closes: Sequence[float], period: int = 14) -> Optional[float]:
    """Wilder's RSI. Returns None if insufficient data."""
    if len(closes) < period + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(d, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0.0)) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def true_ranges(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]) -> list[float]:
    trs = []
    for i in range(1, len(closes)):
        h, l, pc = highs[i], lows[i], closes[i - 1]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return trs


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        period: int = 14) -> Optional[float]:
    """Wilder's ATR (absolute, in price units)."""
    trs = true_ranges(highs, lows, closes)
    if len(trs) < period:
        return None
    a = sum(trs[:period]) / period
    for tr in trs[period:]:
        a = (a * (period - 1) + tr) / period
    return a


def pct_return(closes: Sequence[float], n: int) -> Optional[float]:
    if len(closes) <= n or closes[-1 - n] == 0:
        return None
    return (closes[-1] / closes[-1 - n] - 1) * 100.0


def consecutive_up_sessions(closes: Sequence[float]) -> int:
    n = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] > closes[i - 1]:
            n += 1
        else:
            break
    return n


def daily_returns(closes: Sequence[float]) -> list[float]:
    return [(closes[i] / closes[i - 1] - 1) for i in range(1, len(closes)) if closes[i - 1]]


def stdev(values: Sequence[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    m = sum(values) / len(values)
    var = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(var)


def pivot_high(highs: Sequence[float], lookback: int = 60, exclude_recent: int = 3) -> Optional[float]:
    """Highest swing high of the base (excludes the last few bars so a fresh
    breakout doesn't define its own pivot)."""
    if len(highs) < lookback:
        lookback = len(highs)
    window = highs[-lookback: -exclude_recent] if exclude_recent and lookback > exclude_recent else highs[-lookback:]
    return max(window) if window else None


def base_stats(closes: Sequence[float], highs: Sequence[float], lows: Sequence[float],
               lookback: int = 60) -> dict:
    """Consolidation/base metrics used by the setup classifier."""
    if len(closes) < 20:
        return {}
    win_c = closes[-lookback:]
    win_h = highs[-lookback:]
    win_l = lows[-lookback:]
    hi, lo = max(win_h), min(win_l)
    depth_pct = (hi - lo) / hi * 100 if hi else None

    # Volatility contraction: ATR over the last third vs the first third of base.
    third = max(10, len(win_c) // 3)
    atr_early = atr(win_h[:2 * third], win_l[:2 * third], win_c[:2 * third])
    atr_late = atr(win_h[-third - 1:], win_l[-third - 1:], win_c[-third - 1:])
    vol_contraction = (atr_late < atr_early) if (atr_early and atr_late) else None

    return {
        "base_high": hi,
        "base_low": lo,
        "base_depth_pct": round(depth_pct, 2) if depth_pct is not None else None,
        "base_length": len(win_c),
        "atr_early": atr_early,
        "atr_late": atr_late,
        "volatility_contraction": vol_contraction,
    }


def volume_contraction(volumes: Sequence[float], lookback: int = 30) -> Optional[bool]:
    if len(volumes) < lookback:
        return None
    win = volumes[-lookback:]
    half = len(win) // 2
    early = sum(win[:half]) / half if half else 0
    late = sum(win[half:]) / (len(win) - half) if (len(win) - half) else 0
    if not early:
        return None
    return late < early


def volume_ratio(volumes: Sequence[float], n: int = 1, base: int = 50) -> Optional[float]:
    """Recent volume vs average; >1 means above-average activity."""
    if len(volumes) < base + n:
        return None
    recent = sum(volumes[-n:]) / n
    avg = sum(volumes[-base - n:-n]) / base
    return recent / avg if avg else None


def parabolic_acceleration(closes: Sequence[float]) -> Optional[bool]:
    """True if the most recent 5-bar slope is materially steeper than the prior
    5-bar slope — a sign the move is over-extended."""
    if len(closes) < 11:
        return None
    recent = (closes[-1] / closes[-6] - 1) if closes[-6] else None
    prior = (closes[-6] / closes[-11] - 1) if closes[-11] else None
    if recent is None or prior is None:
        return None
    return recent > 0 and recent > 2 * max(prior, 0.0001)


def higher_low(lows: Sequence[float], lookback: int = 20) -> Optional[bool]:
    """Whether the recent swing low sits above the prior swing low."""
    if len(lows) < lookback * 2:
        return None
    prev = min(lows[-2 * lookback:-lookback])
    recent = min(lows[-lookback:])
    return recent > prev


def extension_metrics(closes: Sequence[float], highs: Sequence[float], lows: Sequence[float],
                      volumes: Sequence[float]) -> dict:
    """Move-extension features. RSI is just ONE of these — never decisive alone."""
    if not closes:
        return {"price": None, "atr": None, "rsi14": None, "dist_from_pivot_atr": None,
                "dist_from_ma20_atr": None, "dist_from_ma50_atr": None, "consecutive_up": 0,
                "parabolic": None, "volume_ratio_1d": None, "ma20": None, "ma50": None, "pivot": None}
    price = closes[-1]
    a = atr(highs, lows, closes) or 0.0
    ma20 = sma(closes, 20)
    ma50 = sma(closes, 50)
    piv = pivot_high(highs)

    def atr_dist(level: Optional[float]) -> Optional[float]:
        if level is None or a == 0:
            return None
        return round((price - level) / a, 2)

    return {
        "price": price,
        "atr": round(a, 4) if a else None,
        "rsi14": rsi(closes),
        "dist_from_pivot_atr": atr_dist(piv),
        "dist_from_ma20_atr": atr_dist(ma20),
        "dist_from_ma50_atr": atr_dist(ma50),
        "consecutive_up": consecutive_up_sessions(closes),
        "parabolic": parabolic_acceleration(closes),
        "volume_ratio_1d": volume_ratio(volumes, 1),
        "ma20": ma20,
        "ma50": ma50,
        "pivot": piv,
    }
