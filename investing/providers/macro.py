"""
investing/providers/macro.py — macro inputs with provenance.

Key changes vs the legacy market-health code:

  * Credit spreads come from a direct FRED OAS series (ICE BofA US High Yield
    OAS, ``BAMLH0A0HYM2``) — NOT an HYG/TLT price ratio proxy.
  * No hardcoded years. Any date range / freshness query is derived from the
    current date at call time.
  * Every value is returned as a DataPoint (source / as_of / status).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Optional
from urllib.request import urlopen

from .. import gateway
from ..data_quality import make_datapoint
from ..schemas import DataPoint

_FRED_KEY = os.environ.get("FRED_API_KEY", "")

# FRED series we care about (direct, not proxied).
FRED_SERIES = {
    "credit_oas": "BAMLH0A0HYM2",   # ICE BofA US High Yield OAS (%)
    "ig_oas": "BAMLC0A0CM",         # ICE BofA US Corporate (IG) OAS (%)
    "yield_10y": "DGS10",
    "yield_curve_10y2y": "T10Y2Y",
}


def _fred_latest(series_id: str) -> tuple[float, str]:
    """Return (value, observation_date_iso) for the latest FRED observation."""
    if not _FRED_KEY:
        raise RuntimeError("FRED_API_KEY not set")
    url = (
        "https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}&api_key={_FRED_KEY}&file_type=json"
        "&sort_order=desc&limit=5"
    )
    with urlopen(url, timeout=10) as r:
        data = json.loads(r.read().decode())
    for obs in data.get("observations", []):
        if obs.get("value") not in (".", "", None):
            return float(obs["value"]), obs["date"]
    raise ValueError("no valid observation")


def fred_point(name: str) -> DataPoint:
    series_id = FRED_SERIES.get(name)
    if not series_id:
        return make_datapoint(name, None, source="FRED", kind="macro", error="unknown series")

    def _fetch():
        return _fred_latest(series_id)

    try:
        value, obs_date = gateway.fetch("FRED", f"fred:{series_id}", _fetch, kind="macro", rate_limit=60)
        as_of = _dt.datetime.fromisoformat(obs_date).replace(tzinfo=_dt.timezone.utc)
        return make_datapoint(name, value, source=f"FRED:{series_id}", kind="macro", as_of=as_of)
    except Exception as e:
        return make_datapoint(name, None, source=f"FRED:{series_id}", kind="macro", error=str(e))


def credit_spread_oas() -> DataPoint:
    """Direct high-yield OAS (the proper credit-stress series)."""
    return fred_point("credit_oas")


def dynamic_news_query(topic: str) -> str:
    """Build a freshness-bounded news query from the *current* date — never a
    hardcoded year."""
    now = _dt.date.today()
    return f"{topic} {now.year} (as of {now.isoformat()})"
