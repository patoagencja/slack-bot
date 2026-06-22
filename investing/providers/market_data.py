"""
investing/providers/market_data.py — price quotes, daily bars and earnings dates.

All access goes through the gateway (retry / circuit breaker / TTL cache). Heavy
deps (yfinance) are imported lazily; when unavailable the provider returns a
DataPoint with status MISSING/ERROR — never a fabricated neutral value.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional

from .. import gateway
from ..data_quality import make_datapoint
from ..schemas import DataPoint


def _yf():
    import yfinance as yf  # lazy
    return yf


def get_quote(ticker: str) -> DataPoint:
    def _fetch():
        yf = _yf()
        t = yf.Ticker(ticker)
        fi = getattr(t, "fast_info", None)
        price = None
        if fi is not None:
            price = fi.get("last_price") if hasattr(fi, "get") else getattr(fi, "last_price", None)
        if price is None:
            hist = t.history(period="5d")
            if len(hist):
                price = float(hist["Close"].iloc[-1])
        if price is None:
            raise ValueError("no price")
        return float(price)

    try:
        price = gateway.fetch("yfinance", f"quote:{ticker}", _fetch, kind="quote", rate_limit=120)
        # yfinance quotes are ~15 min delayed; reflect that in as_of.
        as_of = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=15)
        return make_datapoint(f"{ticker}.price", round(price, 4), source="yfinance",
                              kind="quote", as_of=as_of)
    except Exception as e:
        return make_datapoint(f"{ticker}.price", None, source="yfinance", kind="quote", error=str(e))


def get_bars(ticker: str, period: str = "1y") -> dict:
    """Return {closes, highs, lows, volumes, adv_dollars, point}. ``point`` is a
    DataPoint describing freshness of the bar set."""
    def _fetch():
        yf = _yf()
        hist = yf.Ticker(ticker).history(period=period, auto_adjust=False)
        if hist is None or len(hist) < 2:
            raise ValueError("insufficient bars")
        closes = [float(x) for x in hist["Close"].tolist()]
        highs = [float(x) for x in hist["High"].tolist()]
        lows = [float(x) for x in hist["Low"].tolist()]
        vols = [float(x) for x in hist["Volume"].tolist()]
        last_ts = hist.index[-1].to_pydatetime()
        return {"closes": closes, "highs": highs, "lows": lows, "volumes": vols, "as_of": last_ts}

    try:
        data = gateway.fetch("yfinance", f"bars:{ticker}:{period}", _fetch,
                             kind="daily_bars", rate_limit=120)
        closes, vols = data["closes"], data["volumes"]
        adv = None
        if len(closes) >= 20:
            adv = sum(closes[i] * vols[i] for i in range(-20, 0)) / 20
        as_of = data["as_of"]
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=_dt.timezone.utc)
        point = make_datapoint(f"{ticker}.bars", True, source="yfinance",
                               kind="daily_bars", as_of=as_of)
        return {"closes": closes, "highs": data["highs"], "lows": data["lows"],
                "volumes": vols, "adv_dollars": adv, "point": point}
    except Exception as e:
        return {"closes": [], "highs": [], "lows": [], "volumes": [], "adv_dollars": None,
                "point": make_datapoint(f"{ticker}.bars", None, source="yfinance",
                                        kind="daily_bars", error=str(e))}


def get_earnings_date(ticker: str) -> DataPoint:
    """Next scheduled earnings date with provenance."""
    def _fetch():
        yf = _yf()
        t = yf.Ticker(ticker)
        # prefer get_earnings_dates (richer), fall back to calendar
        try:
            ed = t.get_earnings_dates(limit=8)
            if ed is not None and len(ed):
                today = _dt.date.today()
                future = [idx.date() for idx in ed.index if idx.date() >= today]
                if future:
                    return min(future).isoformat()
        except Exception:
            pass
        cal = t.calendar
        if isinstance(cal, dict):
            val = cal.get("Earnings Date")
            if isinstance(val, (list, tuple)) and val:
                val = val[0]
            if val is not None:
                return val.isoformat() if hasattr(val, "isoformat") else str(val)
        if cal is not None and hasattr(cal, "columns") and "Earnings Date" in cal.columns:
            return str(cal["Earnings Date"].iloc[0])
        raise ValueError("no earnings date")

    try:
        iso = gateway.fetch("yfinance", f"earnings:{ticker}", _fetch, kind="earnings")
        date = _dt.date.fromisoformat(iso[:10])
        return make_datapoint(f"{ticker}.earnings_date", date.isoformat(), source="yfinance",
                              kind="earnings", as_of=_dt.datetime.now(_dt.timezone.utc))
    except Exception as e:
        return make_datapoint(f"{ticker}.earnings_date", None, source="yfinance",
                              kind="earnings", error=str(e))


def days_to(date_iso: Optional[str]) -> Optional[int]:
    if not date_iso:
        return None
    try:
        d = _dt.date.fromisoformat(date_iso[:10])
        return (d - _dt.date.today()).days
    except Exception:
        return None
