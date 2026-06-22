"""Synthetic OHLCV generators for investing unit tests (deterministic, no network)."""

from __future__ import annotations

import math


def gen_breakout(n: int = 200, res: float = 101.0):
    """A tight, volatility-contracting base under a hard resistance, with a small
    smooth breakout in the final two bars (a textbook BREAKOUT)."""
    closes, highs, lows, vols = [], [], [], []
    for i in range(n):
        rng = max(0.9, 4.0 - i * 0.02)
        phase = (i % 8) / 8.0
        c = res - rng * (0.5 + 0.5 * math.cos(2 * math.pi * phase))
        if i >= n - 2:
            c = res + (i - (n - 2)) * 0.18 + 0.18
            hi, v = c + 0.25, 2_500_000
        else:
            hi, v = min(c + 0.3, res), max(300_000, 1_500_000 * (1 - i * 0.003))
        closes.append(round(c, 2)); highs.append(round(hi, 2))
        lows.append(round(c - 0.3, 2)); vols.append(v)
    return closes, highs, lows, vols


def gen_downtrend(n: int = 200):
    """A persistent downtrend — no valid long setup should qualify."""
    closes, highs, lows, vols = [], [], [], []
    p = 200.0
    for i in range(n):
        p *= 0.992
        closes.append(round(p, 2)); highs.append(round(p + 1, 2))
        lows.append(round(p - 1, 2)); vols.append(1_000_000)
    return closes, highs, lows, vols


def gen_uptrend_pullback(n: int = 240):
    """Established uptrend that pulls back shallowly to its rising MA20 on lighter
    volume, staying above MA50 (a PULLBACK_CONTINUATION)."""
    closes, highs, lows, vols = [], [], [], []
    p = 50.0
    for i in range(n):
        if i < n - 6:
            p *= 1.007
            v = 1_300_000
        else:
            p *= 0.990                  # shallow ~6% pullback over 6 bars
            v = 650_000
        closes.append(round(p, 2)); highs.append(round(p + 0.4, 2))
        lows.append(round(p - 0.4, 2)); vols.append(v)
    return closes, highs, lows, vols


def benchmark_series(n: int = 220, drift: float = 1.001):
    closes = []
    p = 400.0
    for _ in range(n):
        p *= drift
        closes.append(round(p, 2))
    return closes
