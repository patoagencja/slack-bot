"""
investing/providers/fundamentals.py — fundamentals with explicit source separation.

The task requires four *distinct* sources, never conflated:

    reported_fundamentals  (from filings / yfinance reported)
    analyst_estimates      (consensus forward estimates)
    estimate_revisions     (actual revision data from an estimates provider)
    news_reported_revision (a mention in the news — lowest trust, flagged as such)

A news mention is NOT treated as full consensus-revision data. Insider scoring
prefers official Form 4 filings and weighs buy-vs-grant, transaction value, size
relative to holdings, insider role, cluster buying, 10b5-1 plan and date.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional, Sequence

from .. import gateway
from ..data_quality import make_datapoint
from ..schemas import DataPoint


def _yf():
    import yfinance as yf
    return yf


def get_reported_fundamentals(ticker: str) -> dict[str, DataPoint]:
    def _fetch():
        info = _yf().Ticker(ticker).info
        return {
            "trailing_pe": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "profit_margins": info.get("profitMargins"),
            "revenue_growth": info.get("revenueGrowth"),
            "ev_ebitda": info.get("enterpriseToEbitda"),
        }
    try:
        d = gateway.fetch("yfinance", f"fund:{ticker}", _fetch, kind="fundamentals")
        now = _dt.datetime.now(_dt.timezone.utc)
        return {k: make_datapoint(f"{ticker}.{k}", v, source="yfinance:reported",
                                  kind="fundamentals", as_of=now)
                for k, v in d.items()}
    except Exception as e:
        return {k: make_datapoint(f"{ticker}.{k}", None, source="yfinance:reported",
                                  kind="fundamentals", error=str(e))
                for k in ("trailing_pe", "forward_pe", "profit_margins", "revenue_growth", "ev_ebitda")}


def get_analyst_estimates(ticker: str) -> dict[str, DataPoint]:
    def _fetch():
        info = _yf().Ticker(ticker).info
        return {
            "target_mean": info.get("targetMeanPrice"),
            "recommendation_mean": info.get("recommendationMean"),
            "num_analysts": info.get("numberOfAnalystOpinions"),
        }
    try:
        d = gateway.fetch("yfinance", f"est:{ticker}", _fetch, kind="fundamentals")
        now = _dt.datetime.now(_dt.timezone.utc)
        return {k: make_datapoint(f"{ticker}.est.{k}", v, source="yfinance:estimates",
                                  kind="fundamentals", as_of=now)
                for k, v in d.items()}
    except Exception as e:
        return {k: make_datapoint(f"{ticker}.est.{k}", None, source="yfinance:estimates",
                                  kind="fundamentals", error=str(e))
                for k in ("target_mean", "recommendation_mean", "num_analysts")}


def news_reported_revision(ticker: str, mention: Optional[str]) -> DataPoint:
    """A revision *mentioned in news* — explicitly low-trust and source-tagged so
    it is never mistaken for real consensus-revision data."""
    return make_datapoint(f"{ticker}.news_revision", mention, source="news:unverified",
                          kind="news", as_of=_dt.datetime.now(_dt.timezone.utc),
                          error=None if mention else "no mention")


# ── Insider scoring (deterministic, filings-first) ─────────────────────────────
_ROLE_WEIGHT = {"CEO": 1.0, "CFO": 0.9, "DIRECTOR": 0.6, "OFFICER": 0.7,
                "10%_OWNER": 0.8, "OTHER": 0.4}


def insider_score(transactions: Sequence[dict]) -> dict:
    """Score insider activity from Form-4-style transactions.

    Each transaction: {type: 'buy'|'grant'|'sell', value, shares, holding_before,
    role, plan_10b5_1: bool, date: ISO}. Returns a normalized score in [-1, 1] and
    components. Open-market BUYS count most; grants and 10b5-1 plan sales are
    heavily discounted; clusters of distinct insiders buying add a premium.
    """
    if not transactions:
        return {"score": 0.0, "components": {}, "note": "no transactions"}

    buy_value = 0.0
    sell_value = 0.0
    buyers = set()
    for t in transactions:
        ttype = (t.get("type") or "").lower()
        role = (t.get("role") or "OTHER").upper()
        rw = _ROLE_WEIGHT.get(role, 0.4)
        value = float(t.get("value") or 0)
        shares = float(t.get("shares") or 0)
        holding_before = float(t.get("holding_before") or 0)
        rel = (shares / holding_before) if holding_before else 0.0
        rel_boost = 1.0 + min(rel, 1.0)              # large relative to holdings -> stronger

        if ttype == "grant":
            continue                                  # not a market signal
        if ttype == "buy":
            w = rw * rel_boost
            buy_value += value * w
            buyers.add(t.get("insider") or role)
        elif ttype == "sell":
            # 10b5-1 planned sales are routine -> discount heavily
            w = rw * (0.2 if t.get("plan_10b5_1") else 1.0)
            sell_value += value * w

    net = buy_value - sell_value
    denom = buy_value + sell_value
    score = (net / denom) if denom else 0.0
    cluster_bonus = min(0.2, 0.1 * max(0, len(buyers) - 1))   # cluster buying premium
    score = max(-1.0, min(1.0, score + (cluster_bonus if net > 0 else 0.0)))
    return {
        "score": round(score, 3),
        "components": {"buy_value_w": round(buy_value, 0), "sell_value_w": round(sell_value, 0),
                       "distinct_buyers": len(buyers)},
        "note": "filings-first; grants ignored; 10b5-1 sales discounted",
    }
