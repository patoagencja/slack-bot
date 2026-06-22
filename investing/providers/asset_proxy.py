"""
investing/providers/asset_proxy.py — NAV provider for asset-proxy equities.

Replaces the hardcoded ``btc_held_approx = 214_400`` in stock_digest. For
companies whose valuation is driven by a balance-sheet asset (e.g. MSTR -> BTC),
this provider assembles:

    underlying asset price, asset units held, cash, debt, convertible debt,
    diluted shares, asset NAV, NAV/share, premium/discount to NAV

Balance-sheet inputs come from a *dated, sourced* registry
(``data/asset_proxies.json``) — operator/filing maintained, never a code
constant — so every value carries an ``as_of`` and a ``source`` and goes stale
through the normal data-quality gate. If a company isn't in the registry, the
fields are MISSING (sentinel), not invented.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Optional

from .. import config
from ..data_quality import make_datapoint
from ..schemas import DataPoint
from . import market_data

_REGISTRY_PATH = os.environ.get(
    "INVEST_ASSET_PROXY_REGISTRY",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                 "data", "asset_proxies.json"),
)


def _load_registry() -> dict:
    try:
        with open(_REGISTRY_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _dp(ticker: str, field: str, value, source: str, as_of_iso: Optional[str]) -> DataPoint:
    as_of = None
    if as_of_iso:
        try:
            as_of = _dt.datetime.fromisoformat(as_of_iso[:10]).replace(tzinfo=_dt.timezone.utc)
        except Exception:
            as_of = None
    if value is None:
        return make_datapoint(f"{ticker}.{field}", None, source=source or "registry",
                              kind="asset_proxy", error="missing in registry")
    return make_datapoint(f"{ticker}.{field}", value, source=source or "registry",
                          kind="asset_proxy", as_of=as_of)


def get_nav(ticker: str) -> dict:
    """Return a dict of DataPoints plus a computed ``summary`` DataPoint.

    Keys: underlying_price, units, cash, debt, convertible_debt, diluted_shares,
    nav_total, nav_per_share, premium_discount, summary.
    """
    reg = _load_registry().get(ticker.upper())
    out: dict[str, DataPoint] = {}

    if not reg:
        for f in ("underlying_price", "units", "cash", "debt", "convertible_debt",
                  "diluted_shares", "nav_total", "nav_per_share", "premium_discount"):
            out[f] = make_datapoint(f"{ticker}.{f}", None, source="asset_proxy_registry",
                                    kind="asset_proxy", error="ticker not in registry")
        out["summary"] = make_datapoint(f"{ticker}.asset_proxy", None, source="asset_proxy_registry",
                                        kind="asset_proxy", error="ticker not in registry")
        return out

    underlying = reg.get("underlying")
    up = market_data.get_quote(underlying) if underlying else make_datapoint(
        f"{ticker}.underlying_price", None, source="config", kind="quote", error="no underlying")
    out["underlying_price"] = up

    bs_source = reg.get("source", "registry")
    as_of = reg.get("as_of")
    out["units"] = _dp(ticker, "units", reg.get("units"), reg.get("units_source", bs_source),
                       reg.get("units_as_of", as_of))
    out["cash"] = _dp(ticker, "cash", reg.get("cash"), bs_source, as_of)
    out["debt"] = _dp(ticker, "debt", reg.get("debt"), bs_source, as_of)
    out["convertible_debt"] = _dp(ticker, "convertible_debt", reg.get("convertible_debt"), bs_source, as_of)
    out["diluted_shares"] = _dp(ticker, "diluted_shares", reg.get("diluted_shares"), bs_source, as_of)

    units = out["units"].value
    cash = out["cash"].value or 0.0
    debt = out["debt"].value or 0.0
    conv = out["convertible_debt"].value or 0.0
    shares = out["diluted_shares"].value
    px = up.value

    nav_total = nav_ps = premium = None
    if units is not None and px is not None:
        holdings_value = units * px
        nav_total = holdings_value + cash - debt - conv
        if shares:
            nav_ps = nav_total / shares
    out["nav_total"] = _dp(ticker, "nav_total", round(nav_total, 0) if nav_total is not None else None,
                           "computed", as_of)
    out["nav_per_share"] = _dp(ticker, "nav_per_share", round(nav_ps, 2) if nav_ps is not None else None,
                               "computed", as_of)

    if nav_ps:
        eq = market_data.get_quote(ticker)
        out["equity_price"] = eq
        if eq.value:
            premium = eq.value / nav_ps - 1
    out["premium_discount"] = _dp(ticker, "premium_discount",
                                  round(premium, 4) if premium is not None else None, "computed", as_of)

    summary_val = None
    if nav_ps is not None:
        summary_val = {"nav_per_share": round(nav_ps, 2),
                       "premium_discount": round(premium, 4) if premium is not None else None,
                       "underlying": underlying, "underlying_price": px}
    out["summary"] = _dp(ticker, "asset_proxy", summary_val, "computed", as_of)
    return out
