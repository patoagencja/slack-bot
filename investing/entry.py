"""
investing/entry.py — orchestrator for the /wejscie position-plan flow.

Pipeline (all deterministic except the optional qualitative LLM enrichment):

    fetch (quotes/bars/earnings/asset-proxy/macro)  -> DataPoints w/ provenance
    -> data-quality gate
    -> relative strength
    -> setup classification
    -> market context (regime, R/R, size multiplier, macro impact)
    -> event risk
    -> portfolio impact
    -> deterministic decision  -> PositionPlan
    -> persist (signal + plan + data-quality events)

Network/heavy deps degrade gracefully: a missing source becomes a MISSING/ERROR
DataPoint and the gate yields DATA_INCOMPLETE — never a fabricated NEUTRAL.
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Optional

from . import (config, data_quality, decision as decision_mod, event_risk,
               market_health, persistence, portfolio, relative_strength,
               setups, universe)
from .schemas import (AssetType, Catalyst, DataPoint, EventRiskAssessment,
                      LLMQualitative, MarketContext, MarketRegime)

logger = logging.getLogger(__name__)


def _asset_type(ticker: str) -> AssetType:
    t = ticker.upper()
    try:
        from .providers import asset_proxy
        import json
        import os
        if os.path.exists(asset_proxy._REGISTRY_PATH):
            with open(asset_proxy._REGISTRY_PATH) as f:
                reg = json.load(f)
            if t in reg:
                return AssetType.CRYPTO_PROXY
    except Exception:
        pass
    if t in universe.KNOWN_ETFS:
        return AssetType.ETF
    return AssetType.EQUITY


def _safe(fn, default=None):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        logger.debug("provider call failed: %s", e)
        return default


def build_market_context(sector: str) -> MarketContext:
    """Build regime from available macro/market data; degrade to UNKNOWN cleanly."""
    scores: dict[str, Optional[float]] = {}
    rate_trend = None
    try:
        from .providers import macro, market_data
        oas = macro.credit_spread_oas()
        if oas.usable() and oas.value is not None:
            # calibrated HY-OAS bands (%), not an arbitrary composite range
            v = oas.value
            scores["credit_oas"] = 1.0 if v < 3 else (0.3 if v < 4.5 else (-0.4 if v < 6 else -1.0))
        spy = market_data.get_bars(universe.BROAD_BENCHMARK, period="1y")
        from . import indicators as ind
        closes = spy.get("closes") or []
        ma200 = ind.sma(closes, 200) if closes else None
        if ma200 and closes:
            pct = (closes[-1] / ma200 - 1) * 100
            scores["spy_vs_ma200"] = max(-1.0, min(1.0, pct / 10.0))
        yc = macro.fred_point("yield_curve_10y2y")
        if yc.usable() and yc.value is not None:
            scores["yield_curve"] = max(-1.0, min(1.0, yc.value))
        y10 = macro.fred_point("yield_10y")
        if y10.usable():
            rate_trend = "rising" if (y10.value or 0) > 4.5 else "stable"
    except Exception as e:
        logger.debug("market context build degraded: %s", e)

    history = _safe(lambda: persistence.market_health_series(250), []) or []
    ctx = market_health.build_context(scores, history, sector=sector, rate_trend=rate_trend)
    # persist for future normalization
    if ctx.health_score is not None:
        composite = (ctx.health_score / 100.0) * 2 - 1
        _safe(lambda: persistence.save_market_health(
            round(composite, 4), ctx.health_percentile, ctx.health_zscore,
            ctx.regime.value, {"scores": scores}))
    ctx.sector_rotation = _sector_rotation_note(sector)
    return ctx


def _sector_rotation_note(sector: str) -> str:
    """Momentum-based note. Named 'rotation', NOT 'inflows' — we do not have real
    ETF flow / units-outstanding data here."""
    bench = universe.SECTOR_BENCHMARK.get(sector)
    if not bench:
        return "brak benchmarku sektorowego"
    try:
        from .providers import market_data
        from . import indicators as ind
        sb = market_data.get_bars(bench, period="6mo").get("closes") or []
        bb = market_data.get_bars(universe.BROAD_BENCHMARK, period="6mo").get("closes") or []
        r_s = ind.pct_return(sb, 20)
        r_b = ind.pct_return(bb, 20)
        if r_s is None or r_b is None:
            return f"{bench}: brak danych momentum"
        diff = round(r_s - r_b, 1)
        tag = "momentum sektora > rynek" if diff > 0 else "momentum sektora < rynek"
        return f"{bench} vs {universe.BROAD_BENCHMARK} 20d: {diff:+}% ({tag})"
    except Exception:
        return f"{bench}: brak danych momentum"


def build_position_plan(
    ticker: str,
    amount: Optional[float] = None,
    risk_pct: Optional[float] = None,
    *,
    strategy: str = config.STRATEGY_POSITION,
    horizon_sessions: int = config.HORIZON_DEFAULT_SESSIONS,
    portfolio_value: Optional[float] = None,
    llm_client: Optional[object] = None,
    persist: bool = True,
):
    """Build and (optionally) persist a :class:`PositionPlan` for ``ticker``."""
    from .providers import market_data, asset_proxy

    ticker = ticker.upper().strip()
    portfolio_value = portfolio_value or amount or config.DEFAULT_PORTFOLIO_VALUE
    risk_pct = risk_pct if risk_pct is not None else config.DEFAULT_RISK_PER_TRADE_PCT
    sector = universe.sector_of(ticker)
    narrative = universe.narrative_of(ticker)
    atype = _asset_type(ticker)

    # ── fetch ──
    price_dp = market_data.get_quote(ticker)
    bars = market_data.get_bars(ticker, period="1y")
    bars_dp: DataPoint = bars["point"]
    earnings_dp = market_data.get_earnings_date(ticker)
    broad = market_data.get_bars(universe.BROAD_BENCHMARK, period="1y")
    sector_bench = universe.sector_benchmark(ticker)
    sector_bars = market_data.get_bars(sector_bench, period="1y") if sector_bench else {"closes": []}

    points: dict[str, DataPoint] = {
        "price": price_dp,
        "bars": bars_dp,
        "earnings_date": earnings_dp,
    }
    required = ["price", "bars", "earnings_date"]
    optional = ["benchmark_bars"]
    points["benchmark_bars"] = broad["point"] if "point" in broad else DataPoint(name="benchmark_bars")

    if atype == AssetType.CRYPTO_PROXY:
        nav = asset_proxy.get_nav(ticker)
        points["asset_proxy_nav"] = nav.get("summary", DataPoint(name="asset_proxy_nav"))
        required.append("asset_proxy_nav")

    # log data-quality events
    if persist:
        for name, dp in points.items():
            if dp.status.value in ("MISSING", "ERROR", "STALE"):
                _safe(lambda n=name, d=dp: persistence.log_data_quality_event(
                    ticker, n, d.status.value, d.source, d.note))

    # ── relative strength ──
    rs = {}
    if bars["closes"] and broad["closes"]:
        rs = relative_strength.compute(bars["closes"], broad["closes"],
                                       sector_bars.get("closes") or None)

    # ── earnings / event timing ──
    days_to_earn = market_data.days_to(earnings_dp.value) if earnings_dp.value else None
    blackout_days = config.EVENT_BLACKOUT_SESSIONS * 7.0 / 5.0
    imminent = days_to_earn is not None and 0 <= days_to_earn <= blackout_days

    # ── optional qualitative LLM enrichment (qualitative only) ──
    llm: Optional[LLMQualitative] = None
    catalysts: list[Catalyst] = []
    if llm_client is not None or _llm_available():
        llm = _safe(lambda: _qualitative(ticker, sector, rs, llm_client))
        if llm:
            catalysts = llm.catalysts

    # ── setup ──
    feat = setups.build_features(
        bars["closes"], bars["highs"], bars["lows"], bars["volumes"], rs,
        imminent_binary_event=imminent,
    )
    setup = setups.classify(feat)

    # ── data-quality gate ──
    gate = data_quality.evaluate(points, required=required, optional=optional)

    # ── market context & event risk ──
    market = build_market_context(sector)
    earn_date = None
    if earnings_dp.value:
        try:
            earn_date = _dt.date.fromisoformat(str(earnings_dp.value)[:10])
        except Exception:
            earn_date = None
    event = event_risk.assess(
        earnings_date=earn_date, days_to_earnings=days_to_earn,
        catalysts=catalysts, setup_type=setup.setup_type,
    )

    # ── portfolio impact (size first to know quantity) ──
    # provisional sizing uses the planned entry from the setup
    entry_ref = setup.entry_zone[0] if setup.entry_zone else (price_dp.value or 0)
    from . import sizing as sizing_mod
    prov = sizing_mod.size_position(
        entry_price=max(entry_ref, 0.01),
        stop_price=max(setup.stop or 0.01, 0.01),
        portfolio_value=portfolio_value, risk_per_trade_pct=risk_pct,
        adv_dollars=bars.get("adv_dollars"), size_multiplier=market.size_multiplier,
    ) if (setup.stop and entry_ref) else None
    prov_qty = prov.final_quantity if prov else 0
    pf_impact = portfolio.evaluate_new_position(
        ticker=ticker, sector=sector, narrative=narrative,
        entry_price=max(entry_ref, 0.01), stop_price=max(setup.stop or 0.01, 0.01),
        quantity=prov_qty, beta=rs.get("beta"), portfolio_value=portfolio_value,
    )

    # ── decision ──
    snapshot = {
        "rs": rs, "setup_features": {k: feat["ext"].get(k) for k in
                                     ("rsi14", "atr", "dist_from_pivot_atr", "pivot")},
        "base": feat["base"], "data_points": {k: v.model_dump(mode="json")
                                              for k, v in points.items()},
    }
    plan = decision_mod.decide(
        ticker=ticker, strategy=strategy, horizon_sessions=horizon_sessions,
        asset_type=atype, price_point=price_dp, gate=gate, setup=setup,
        market=market, event=event, portfolio_impact=pf_impact,
        portfolio_value=portfolio_value, risk_per_trade_pct=risk_pct, sector=sector,
        adv_dollars=bars.get("adv_dollars"), llm=llm, rs=rs, feature_snapshot=snapshot,
    )

    if persist:
        _safe(lambda: persistence.log_signal(ticker, strategy, setup.setup_type.value,
                                             plan.signal_confidence, plan.data_quality_score,
                                             {"reason": plan.decision_reason}))
        _safe(lambda: persistence.save_position_plan(plan))
    return plan


def _llm_available() -> bool:
    try:
        import _ctx
        return getattr(_ctx, "claude", None) is not None
    except Exception:
        return False


def _qualitative(ticker, sector, rs, client) -> Optional[LLMQualitative]:
    from . import llm as llm_mod
    system = (
        "Jesteś analitykiem akcji. Wyciągasz WYŁĄCZNIE oceny jakościowe z newsów i "
        "kontekstu. NIE podajesz cen, poziomów stopa, liczby akcji, score'u ani "
        "decyzji kup/sprzedaj — te wylicza kod. Skup się na tezie, bull/bear case, "
        "katalizatorach i sprzecznościach."
    )
    user = (
        f"Ticker: {ticker} (sektor {sector}). RS: {rs}. "
        "Zbuduj zwięzły bull case, bear case, listę katalizatorów (z rodzajem i "
        "horyzontem) oraz sprzeczności. Tylko ocena jakościowa."
    )
    return llm_mod.extract_qualitative(system=system, user=user, client=client)
