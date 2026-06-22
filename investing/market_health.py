"""
investing/market_health.py — market regime via historical normalization.

Replaces the old fixed "-58…+73" linear scaling. Composite is normalized against
its own history (percentile + z-score) and mapped to a regime by calibrated
percentile bands. Key rules:

  * Missing indicators are excluded from the aggregate — never treated as zero.
  * Indicators are grouped into themes and each theme contributes once, so VIX /
    breadth / SPY-vs-MA200 / risk-off rotation / Fear&Greed cannot multiply-count
    the same underlying phenomenon.
  * Macro never flips every name to "wait". It only adjusts required R/R, the
    position-size multiplier and the portfolio-heat limit, and produces a
    *sector-specific* macro-impact note.
"""

from __future__ import annotations

import math
from typing import Optional

from . import config
from .schemas import MarketContext, MarketRegime

# Indicator -> theme. Each theme is aggregated once (de-correlation).
THEME_OF = {
    "vix_structure": "volatility",
    "skew": "volatility",
    "put_call": "volatility",
    "breadth": "breadth",
    "spy_vs_ma200": "trend",
    "credit_oas": "credit",          # FRED OAS, not HYG/TLT (see providers.macro)
    "rotation": "rotation",
    "fear_greed": "sentiment",
    "naaim": "sentiment",
    "earnings_revisions": "fundamental_breadth",
    "yield_curve": "rates",
    "dxy": "rates",
}

THEME_WEIGHTS = {
    "volatility": 1.2,
    "breadth": 1.0,
    "trend": 1.0,
    "credit": 1.2,
    "rotation": 1.0,
    "sentiment": 0.9,
    "fundamental_breadth": 1.0,
    "rates": 0.8,
}


def _percentile(value: float, history: list[float]) -> Optional[float]:
    pop = [h for h in history if h is not None]
    if len(pop) < 8:                      # not enough history to be meaningful
        return None
    below = sum(1 for h in pop if h < value)
    equal = sum(1 for h in pop if h == value)
    return round((below + 0.5 * equal) / len(pop) * 100, 1)


def _zscore(value: float, history: list[float]) -> Optional[float]:
    pop = [h for h in history if h is not None]
    if len(pop) < 8:
        return None
    m = sum(pop) / len(pop)
    var = sum((h - m) ** 2 for h in pop) / (len(pop) - 1)
    sd = math.sqrt(var)
    return round((value - m) / sd, 2) if sd else 0.0


def compute_composite(indicator_scores: dict[str, Optional[float]]) -> tuple[Optional[float], float, list[str]]:
    """Aggregate per-theme (each theme averaged then weighted). Returns
    (composite or None, confidence 0-1, missing_indicator_names).

    Each indicator score is expected in [-1, 1] (sign = bull/bear). Missing values
    are excluded — confidence reflects how many themes had data."""
    themes: dict[str, list[float]] = {}
    missing: list[str] = []
    for name, theme in THEME_OF.items():
        v = indicator_scores.get(name)
        if v is None:
            missing.append(name)
            continue
        themes.setdefault(theme, []).append(max(-1.0, min(1.0, v)))

    if not themes:
        return None, 0.0, missing

    num = 0.0
    den = 0.0
    for theme, vals in themes.items():
        w = THEME_WEIGHTS.get(theme, 1.0)
        num += w * (sum(vals) / len(vals))   # average within theme -> counted once
        den += w
    composite = num / den if den else None    # in [-1, 1]

    total_themes = len(set(THEME_OF.values()))
    confidence = round(len(themes) / total_themes, 2)
    return (round(composite, 4) if composite is not None else None), confidence, missing


def regime_from(composite: Optional[float], percentile: Optional[float]) -> MarketRegime:
    """Prefer historical percentile; fall back to calibrated static bands on the
    [-1,1] composite when history is insufficient."""
    if percentile is not None:
        if percentile >= 70:
            return MarketRegime.BULL
        if percentile >= 45:
            return MarketRegime.CAUTION
        if percentile >= 25:
            return MarketRegime.DEFENSIVE
        return MarketRegime.BEAR
    if composite is None:
        return MarketRegime.UNKNOWN
    if composite >= 0.30:
        return MarketRegime.BULL
    if composite >= 0.0:
        return MarketRegime.CAUTION
    if composite >= -0.30:
        return MarketRegime.DEFENSIVE
    return MarketRegime.BEAR


def build_context(
    indicator_scores: dict[str, Optional[float]],
    history: Optional[list[float]] = None,
    *,
    sector: str = "UNKNOWN",
    rate_trend: Optional[str] = None,
) -> MarketContext:
    history = history or []
    composite, confidence, missing = compute_composite(indicator_scores)
    pct = _percentile(composite, history) if composite is not None else None
    z = _zscore(composite, history) if composite is not None else None
    regime = regime_from(composite, pct)

    ctx = MarketContext(
        regime=regime,
        health_score=round((composite + 1) / 2 * 100, 1) if composite is not None else None,
        health_percentile=pct,
        health_zscore=z,
        required_rr=config.RR_MIN_BY_REGIME.get(regime.value, config.RR_MIN_BY_REGIME["UNKNOWN"]),
        size_multiplier=config.SIZE_MULT_BY_REGIME.get(regime.value, config.SIZE_MULT_BY_REGIME["UNKNOWN"]),
    )
    ctx.macro_impact = _sector_macro_impact(regime, sector, rate_trend)
    if missing:
        ctx.notes.append(f"Brakujące wskaźniki (pominięte, NIE zerowane): {', '.join(missing)}")
    ctx.notes.append(f"Pewność reżimu: {confidence:.0%} (pokrycie tematów)")
    return ctx


# Sectors whose macro sensitivity differs materially — macro impact is per-sector,
# not a blanket market-wide verdict.
_RATE_SENSITIVE = {"Tech/Cloud", "AI Apps", "AI/Semis", "Space/Defense"}
_DEFENSIVE = {"Healthcare", "Defense"}


def _sector_macro_impact(regime: MarketRegime, sector: str, rate_trend: Optional[str]) -> str:
    base = {
        MarketRegime.BULL: "Makro sprzyja — korekty to okazje.",
        MarketRegime.CAUTION: "Makro mieszane — podniesiony próg R/R i mniejszy size.",
        MarketRegime.DEFENSIVE: "Makro defensywne — tylko najlepsze setupy, zmniejszony size.",
        MarketRegime.BEAR: "Makro negatywne — minimalna ekspozycja, wymagane wysokie R/R.",
        MarketRegime.UNKNOWN: "Brak pełnego obrazu makro.",
    }[regime]
    if rate_trend == "rising" and sector in _RATE_SENSITIVE:
        base += f" Sektor {sector} wrażliwy na rosnące stopy — dodatkowy headwind."
    elif regime in (MarketRegime.DEFENSIVE, MarketRegime.BEAR) and sector in _DEFENSIVE:
        base += f" Sektor {sector} defensywny — relatywnie odporny w tym reżimie."
    return base
