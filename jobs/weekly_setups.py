"""
jobs/weekly_setups.py — Swing trade setups for the coming week.

Scans full market (S&P 500 + Nasdaq 100 + watchlist + top-100 crypto).
Scheduled: Friday 16:00 UTC.
Commands:  /swing | /swing {N} | /swing scan | /swing watchlist | /swing {sektor} | /swing {TICKER}
"""

import re
import json
import logging
import datetime
import warnings
import time as _time

import yfinance as yf
import pandas as pd

import _ctx
from jobs.stock_digest import (
    WATCHLIST,
    _calc_rsi,
    _calc_technicals,
    _fetch_btc_dominance,
    _fetch_top_coins,
    fetch_macro_briefing,
    STOCK_CHANNEL_ID,
)

try:
    from tavily import TavilyClient as _TavilyClient
    import os as _os
    _TAVILY_KEY = _os.environ.get("TAVILY_API_KEY", "")
    _tavily = _TavilyClient(api_key=_TAVILY_KEY) if _TAVILY_KEY else None
except ImportError:
    _tavily = None

logger = logging.getLogger(__name__)


# ── Extended sector universe (beyond watchlist) ───────────────────────────────
_SECTOR_UNIVERSE: dict[str, list[str]] = {
    "space":    ["RKLB", "ASTS", "LUNR", "PL", "RDW", "IRDM", "SPCE", "MAXR", "BWXT", "GEO"],
    "nuclear":  ["CCJ", "UEC", "DNN", "UUUU", "NNE", "SMR", "OKLO", "LEU", "BWXT", "VST"],
    "defense":  ["NOC", "LMT", "RTX", "GD", "HII", "TDG", "AXON", "BA", "KTOS", "PLTR", "LDOS", "SAIC", "BAH"],
    "ai":       ["NVDA", "AMD", "AVGO", "MSFT", "META", "GOOGL", "APP", "CRWD", "NOW", "ALAB", "SMCI", "ARM", "MRVL"],
    "biotech":  ["NVO", "LLY", "ISRG", "REGN", "VRTX", "GILD", "MRNA", "TEM", "TDOC", "RXRX", "ARKG", "ALNY", "RARE"],
    "fintech":  ["PYPL", "SQ", "AFRM", "NU", "DLO", "MELI", "COIN", "HOOD", "SOFI", "OPEN", "UPST"],
    "cyber":    ["CRWD", "FTNT", "PANW", "S", "RBRK", "ZS", "CYBR", "TENB", "RPD"],
    "semis":    ["NVDA", "AMD", "AVGO", "ASML", "QCOM", "MU", "AMAT", "KLAC", "LRCX", "ALAB", "MRVL", "AMBA", "ON"],
    "energy":   ["EOG", "COP", "DVN", "FANG", "MPC", "VLO", "PSX", "CCJ", "UEC"],
    "consumer": ["LULU", "NKE", "DECK", "CMG", "RACE", "SBUX", "MCD", "YUM", "BURL", "ROST"],
}

_SECTOR_ALIASES = {
    "space": "space", "kosmiczny": "space", "kosmos": "space",
    "nuclear": "nuclear", "nuklear": "nuclear", "uranium": "nuclear", "uran": "nuclear",
    "defense": "defense", "defence": "defense", "obronny": "defense", "obrona": "defense",
    "ai": "ai", "sztuczna": "ai", "tech": "ai",
    "biotech": "biotech", "bio": "biotech", "zdrowie": "biotech", "glp1": "biotech",
    "fintech": "fintech", "em": "fintech",
    "cyber": "cyber", "cybersecurity": "cyber",
    "semis": "semis", "chips": "semis", "semiconductor": "semis",
    "energy": "energy", "energia": "energy",
    "consumer": "consumer", "konsument": "consumer",
}

# Tickers that belong to an active narrative (for narrative bonus scoring)
_NARRATIVE_TICKERS: dict[str, str] = {}
for _s, _tickers in _SECTOR_UNIVERSE.items():
    for _t in _tickers:
        if _t not in _NARRATIVE_TICKERS:
            _NARRATIVE_TICKERS[_t] = _s


# ── Broad market universe ─────────────────────────────────────────────────────

_SP500_FALLBACK = [
    "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "META", "TSLA", "AVGO", "BRK-B",
    "JPM", "LLY", "V", "ORCL", "MA", "XOM", "COST", "HD", "PG", "JNJ", "UNH",
    "NFLX", "ABBV", "CRM", "AMD", "BAC", "KO", "MRK", "CVX", "ADBE", "WMT",
    "TMO", "ACN", "LIN", "MCD", "ABT", "TXN", "NEE", "CSCO", "DHR", "AMGN",
    "NKE", "IBM", "PM", "QCOM", "INTU", "CAT", "GS", "PEP", "HON", "SPGI",
    "ISRG", "T", "BKNG", "AMAT", "LOW", "NOW", "SYK", "VRTX", "PLD", "PANW",
    "CME", "GILD", "AXP", "MU", "CI", "GE", "LRCX", "SO", "BSX", "REGN",
    "MMC", "KLAC", "DUK", "ADP", "EOG", "MO", "ZTS", "MRNA", "SLB", "APD",
    "MCO", "NOC", "RTX", "LMT", "HCA", "F", "GM", "INTC", "CARR", "UBER",
    "ABNB", "RIVN", "LCID", "PLTR", "SNOW", "CRWD", "DDOG", "ZS", "FTNT",
    "TTD", "RBLX", "U", "APP", "SOFI", "COIN", "HOOD", "AFRM", "UPST",
    "SMCI", "ARM", "MRVL", "ON", "AMBA", "WOLF",
    "AXON", "TDG", "GD", "HII", "LDOS", "SAIC", "KTOS", "BAH",
    "CCJ", "UEC", "DNN", "UUUU", "NNE", "SMR", "OKLO",
    "RKLB", "ASTS", "LUNR", "PL", "RDW", "IRDM",
    "NU", "DLO", "MELI", "SE", "GRAB",
    "DECK", "LULU", "RACE", "CMG",
    "TEM", "TDOC", "RXRX",
    "MSTR", "MARA",
]

_NDX_FALLBACK = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA", "GOOGL", "AVGO", "COST",
    "NFLX", "AMD", "ADBE", "PEP", "CSCO", "INTC", "INTU", "QCOM", "AMAT",
    "HON", "SBUX", "PYPL", "GILD", "REGN", "VRTX", "ADP", "LRCX", "PANW",
    "KLAC", "SNPS", "CDNS", "MRVL", "CRWD", "FTNT", "ZS", "DDOG", "OKTA",
    "TEAM", "MDB", "WDAY", "NOW", "SNOW", "TTD", "RBLX", "UBER", "ABNB",
    "BKNG", "TRIP", "EXPE", "LCID", "RIVN", "ARM",
]


def _fetch_sp500_tickers() -> list[str]:
    try:
        tables = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
        tickers = tables[0]["Symbol"].str.replace(".", "-", regex=False).tolist()
        if len(tickers) > 400:
            return tickers
    except Exception as e:
        logger.warning("S&P 500 Wikipedia fetch failed: %s", e)
    return _SP500_FALLBACK


def _fetch_ndx_tickers() -> list[str]:
    try:
        tables = pd.read_html("https://en.wikipedia.org/wiki/Nasdaq-100")
        for t in tables:
            if "Ticker" in t.columns and len(t) > 90:
                return t["Ticker"].tolist()
    except Exception as e:
        logger.warning("NDX Wikipedia fetch failed: %s", e)
    return _NDX_FALLBACK


def _build_scan_universe(mode: str = "all") -> list[str]:
    """
    mode: 'all' = S&P500 + NDX + watchlist
          'watchlist' = only WATCHLIST
          any key in _SECTOR_UNIVERSE = that sector's extended list
    """
    if mode == "watchlist":
        return list(WATCHLIST)

    sector = _SECTOR_ALIASES.get(mode.lower())
    if sector and sector in _SECTOR_UNIVERSE:
        # Sector tickers + matching watchlist tickers
        sector_list = _SECTOR_UNIVERSE[sector]
        combined = list(dict.fromkeys(sector_list + list(WATCHLIST)))
        return combined

    # mode == "all": full market scan
    sp500  = _fetch_sp500_tickers()
    ndx    = _fetch_ndx_tickers()
    combined = list(dict.fromkeys(sp500 + ndx + list(WATCHLIST)))
    return combined


# ── ATR and trend helpers ─────────────────────────────────────────────────────

def _calc_atr_pct(hist_df: "pd.DataFrame", period: int = 14) -> float:
    """ATR(14) as % of current price — proxy for realistic single-swing move."""
    if len(hist_df) < period + 1:
        return 0.0
    try:
        high  = hist_df["High"]
        low   = hist_df["Low"]
        close = hist_df["Close"]
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low  - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr   = float(tr.rolling(period).mean().iloc[-1])
        price = float(close.iloc[-1])
        return round(atr / price * 100, 2) if price else 0.0
    except Exception:
        return 0.0


def _calc_ma50_slope(closes: list) -> bool:
    """True if MA50 is pointing upward (today vs 10 sessions ago)."""
    if len(closes) < 60:
        return False
    s = pd.Series(closes)
    ma_now = float(s.rolling(50).mean().iloc[-1])
    ma_ago = float(s.rolling(50).mean().iloc[-11])
    return ma_now > ma_ago


# ── Batch pre-screen ──────────────────────────────────────────────────────────

def _batch_prescreen(tickers: list[str], qqq_30d: float | None = None,
                     min_dv_m: float = 5.0) -> list[dict]:
    """
    Fast batch screen via yfinance download.
    Returns list of dicts with basic metrics for each candidate that passes.
    Typical: 500 tickers → 30-60 candidates.
    """
    candidates = []
    chunk_size = 50  # smaller chunks = more reliable downloads

    # Silence yfinance delisted / crumb warnings
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i:i + chunk_size]
        raw = None
        for _attempt in range(3):
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    raw = yf.download(
                        chunk, period="65d", progress=False,
                        auto_adjust=True, timeout=30,
                    )
                break
            except Exception as e:
                err_str = str(e)
                if "401" in err_str or "Unauthorized" in err_str or "Crumb" in err_str:
                    logger.warning("yfinance 401 crumb error on chunk %d, retry %d/3", i // chunk_size, _attempt + 1)
                    _time.sleep(2 ** _attempt)
                    # Reset yfinance session to get fresh crumb
                    try:
                        yf.utils.get_json.cache_clear()
                    except Exception:
                        pass
                else:
                    logger.warning("yfinance download chunk %d error: %s", i // chunk_size, e)
                    break
        if raw is None or raw.empty:
            continue

        # Detect column structure (single vs multi-ticker)
        if isinstance(raw.columns, pd.MultiIndex):
            closes_df  = raw["Close"]
            volumes_df = raw.get("Volume")
        else:
            # Single-ticker download — wrap in DataFrame
            closes_df  = raw[["Close"]].rename(columns={"Close": chunk[0]})
            volumes_df = raw[["Volume"]].rename(columns={"Volume": chunk[0]}) if "Volume" in raw.columns else None

        for t in chunk:
            try:
                if t not in closes_df.columns:
                    continue
                c = closes_df[t].dropna()
                if len(c) < 22:
                    continue

                price = float(c.iloc[-1])
                if price < 5:
                    continue  # penny stock filter

                # Trend: must be above MA50
                ma50 = float(c.rolling(50).mean().iloc[-1]) if len(c) >= 50 else float(c.mean())
                if price < ma50:
                    continue

                # ATH filter: skip stocks within 5% of 52-week high (early reject)
                high52 = float(c.tail(252).max()) if len(c) >= 252 else float(c.max())
                if (price / high52 - 1) * 100 > -5:
                    continue

                # RSI filter
                rsi = round(_calc_rsi(c.tolist()), 1)
                if not (40 <= rsi <= 70):
                    continue

                # Liquidity check
                avg_dv_m = 0.0
                vol_spike = 1.0
                if volumes_df is not None and t in volumes_df.columns:
                    v = volumes_df[t].dropna()
                    common = c.index.intersection(v.index)
                    if len(common) >= 20:
                        avg_dv_m = float((v.loc[common] * c.loc[common]).tail(20).mean()) / 1_000_000
                        avg_vol = float(v.tail(20).mean())
                        recent_vol = float(v.tail(3).mean())
                        vol_spike = round(recent_vol / avg_vol, 2) if avg_vol else 1.0
                if avg_dv_m < min_dv_m:
                    continue

                # Momentum vs QQQ (relative strength)
                momentum_30d = round((price / float(c.iloc[-30]) - 1) * 100, 2) if len(c) >= 30 else 0.0
                rs_vs_qqq = round(momentum_30d - qqq_30d, 2) if qqq_30d is not None else 0.0
                if rs_vs_qqq < -10:
                    continue  # badly lagging market

                candidates.append({
                    "ticker":       t,
                    "price":        round(price, 2),
                    "rsi":          rsi,
                    "momentum_30d": momentum_30d,
                    "rs_vs_qqq":    rs_vs_qqq,
                    "avg_dv_m":     round(avg_dv_m, 1),
                    "vol_spike":    vol_spike,
                    "ma50":         round(ma50, 2),
                })
            except Exception:
                continue

    return candidates


# ── Score calculation ─────────────────────────────────────────────────────────

def _calc_swing_score(c: dict) -> int:
    """Score 0-100 based on trend, momentum, RSI, catalyst, narrative."""
    score = 0

    # Trend (30 pts)
    if c.get("ma50_slope_up"):   score += 10
    if c.get("above_ma200"):     score += 10
    pct50 = c.get("pct_from_ma50", 99)
    if 0 < pct50 < 5:            score += 10  # tight consolidation near MA50
    elif 5 <= pct50 < 12:        score += 5

    # Momentum (25 pts)
    rs = c.get("rs_vs_qqq", 0)
    if rs > 10:    score += 15
    elif rs > 2:   score += 10
    elif rs > -3:  score += 5
    vs = c.get("vol_spike", 1.0)
    if vs >= 1.5:  score += 10
    elif vs >= 1.2: score += 5

    # RSI (20 pts)
    rsi = c.get("rsi", 50)
    if 50 <= rsi <= 65:           score += 20
    elif 45 <= rsi < 50 or 65 < rsi <= 70: score += 12
    elif 40 <= rsi < 45:          score += 5

    # Catalyst (15 pts)
    if c.get("catalyst"):         score += 15

    # Narrative (10 pts)
    if c.get("narrative_sector"): score += 10

    return min(100, score)


# ── Pattern detection ─────────────────────────────────────────────────────────

def _detect_pattern(closes: list, ticker: str = "") -> dict:
    """Identify technical setup from price history."""
    if len(closes) < 22:
        return {"pattern": "insufficient_data", "quality": 0}

    price = closes[-1]
    s     = pd.Series(closes)

    ma50  = float(s.rolling(50).mean().iloc[-1]) if len(closes) >= 50 else float(s.mean())
    ma200 = float(s.rolling(200).mean().iloc[-1]) if len(closes) >= 200 else None

    above_ma50  = price > ma50
    above_ma200 = (price > ma200) if ma200 else None

    # 3-week range (15 trading days)
    recent_high = max(closes[-15:])
    recent_low  = min(closes[-15:])
    range_pct   = (recent_high - recent_low) / recent_low * 100 if recent_low else 0

    # Prior move: last 30 trading days
    prior_move = (closes[-1] / closes[-30] - 1) * 100 if len(closes) >= 30 else 0

    # 52-week range position
    high52 = max(closes[-252:]) if len(closes) >= 252 else max(closes)
    pct_from_high52 = (price - high52) / high52 * 100

    # Breakout: today's price > 15-day high (excluding today)
    is_breakout = len(closes) >= 16 and price > max(closes[-16:-1])

    # Flag: strong prior move + tight consolidation
    is_flag = prior_move > 10 and range_pct < 6

    # MA50 bounce: within 4% of MA50, price above MA50
    pct_from_ma50 = (price - ma50) / ma50 * 100
    is_ma50_bounce = above_ma50 and 0 < pct_from_ma50 < 4

    # MA200 bounce
    pct_from_ma200 = (price - ma200) / ma200 * 100 if ma200 else None
    is_ma200_bounce = (ma200 and above_ma200 and pct_from_ma200 is not None
                       and 0 < pct_from_ma200 < 5)

    # Upper quartile but not top 5% of 52-week range — ideal momentum zone
    in_momentum_zone = -25 < pct_from_high52 < -5

    if is_breakout and above_ma50 and in_momentum_zone:
        pattern = "Breakout ponad opór 15-dniowy"
        quality = 4
    elif is_breakout and above_ma50:
        pattern = "Breakout ponad opór 15-dniowy"
        quality = 3
    elif is_flag and above_ma50:
        pattern = f"Flag — ruch +{prior_move:.0f}% + konsolidacja {range_pct:.1f}%"
        quality = 3
    elif is_ma200_bounce:
        pattern = f"Odbicie od MA200 (${ma200:.0f})"
        quality = 3
    elif is_ma50_bounce and in_momentum_zone:
        pattern = f"Odbicie od MA50 (${ma50:.0f}) — momentum zone"
        quality = 3
    elif is_ma50_bounce:
        pattern = f"Odbicie od MA50 (${ma50:.0f})"
        quality = 2
    else:
        pattern = "Brak wyraźnego setupu"
        quality = 0

    return {
        "pattern":        pattern,
        "quality":        quality,
        "above_ma50":     above_ma50,
        "above_ma200":    above_ma200,
        "pct_from_ma50":  round(pct_from_ma50, 1),
        "pct_from_high52": round(pct_from_high52, 1),
        "prior_move_30d": round(prior_move, 1),
        "range_pct":      round(range_pct, 1),
    }


def _calc_rr(closes: list, atr_pct: float = 0) -> dict:
    """Entry / target / stop / R:R. Uses ATR for dynamic levels when available."""
    if len(closes) < 20:
        return {}
    price = closes[-1]
    if atr_pct > 0:
        # ATR-based: stop = 1.5× ATR, target = 3× ATR → natural 2:1 R/R
        # Favours volatile stocks capable of 10%+ moves
        stop_price = round(price * (1 - atr_pct / 100 * 1.5), 2)
        target     = round(price * (1 + atr_pct / 100 * 3.0), 2)
    else:
        resistance = max(closes[-23:-3]) if len(closes) >= 23 else max(closes[:-1])
        stop_price = round(price * 0.955, 2)
        target     = round(max(resistance, price * 1.09), 2)
    tgt_pct = round((target - price) / price * 100, 1)
    stp_pct = round((stop_price - price) / price * 100, 1)
    rr      = round(tgt_pct / abs(stp_pct), 1) if stp_pct else 0
    return {
        "entry":      round(price, 2),
        "target":     target,
        "target_pct": tgt_pct,
        "stop":       stop_price,
        "stop_pct":   stp_pct,
        "rr_ratio":   rr,
    }


# ── Per-ticker full analysis ──────────────────────────────────────────────────

def _analyze_setup_ticker(ticker: str, prescreen: dict | None = None,
                          fetch_catalyst: bool = True) -> dict | None:
    """
    Full analysis for one ticker: yfinance history + ATR + pattern + optional catalyst.
    prescreen: pre-computed basic metrics from batch download (saves one API call).
    fetch_catalyst=False skips Tavily to allow parallel batch analysis.
    """
    try:
        t = yf.Ticker(ticker)

        # ── Price: use prescreen (from batch download) + freshness via fast_info ──
        # Avoid t.info — it's the slowest yfinance call and frequently hangs
        price = prescreen["price"] if prescreen else 0.0

        # Freshness check: pre/post-market gap via fast_info (fast, non-blocking)
        try:
            live_price = getattr(t.fast_info, "last_price", None)
            if live_price and live_price > 1:
                gap_pct = (live_price - price) / price * 100 if price else 0
                if gap_pct < -5:
                    logger.info("Skip %s: gap %.1f%% (%s→%s)", ticker, gap_pct, price, live_price)
                    return None
                price = live_price
        except Exception:
            pass

        if not price:
            return None

        # ── History: use pre-fetched df if passed, else fetch ────────────────────
        hist = prescreen.get("_hist") if prescreen else None
        if hist is None or (hasattr(hist, "empty") and hist.empty):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                hist = t.history(period="1y", timeout=15)
        if hist is None or hist.empty or len(hist) < 22:
            return None
        closes = hist["Close"].tolist()

        rsi      = round(_calc_rsi(closes), 1)
        tech     = _calc_technicals(closes)
        pattern  = _detect_pattern(closes, ticker)
        atr_pct  = _calc_atr_pct(hist)
        rr       = _calc_rr(closes, atr_pct=atr_pct)
        slope_up = _calc_ma50_slope(closes)

        # Potential move: ATR × 3 (what target % would be)
        potential_pct = round(atr_pct * 3, 1)

        # Hard filters
        if not (40 <= rsi <= 70):
            return None
        if not tech.get("above_ma50"):
            return None
        if pattern["quality"] < 1:
            return None

        # ATH filter: reject stocks within 5% of 52-week high
        # At ATH there's limited upside and high reversal risk — not a swing setup
        high52 = max(closes[-252:]) if len(closes) >= 252 else max(closes)
        pct_from_high52 = (price / high52 - 1) * 100
        if pct_from_high52 > -5:
            return None

        # Liquidity filter: need avg dollar volume ≥ $5M
        avg_dv_m = prescreen.get("avg_dv_m", 999) if prescreen else 999
        if avg_dv_m < 5.0:
            # Re-check from history if not pre-screened
            try:
                avg_dv_m = float(
                    (hist["Volume"] * hist["Close"]).tail(20).mean()
                ) / 1_000_000
            except Exception:
                avg_dv_m = 999
        if avg_dv_m < 5.0:
            return None

        # Potential move filter: ATR × 3 must give at least 8% upside
        if potential_pct < 8.0:
            return None

        # Volume spike
        vol_spike = prescreen.get("vol_spike", 1.0) if prescreen else 1.0
        rs_vs_qqq = prescreen.get("rs_vs_qqq", 0.0) if prescreen else 0.0

        # Catalyst (Tavily) — skipped in parallel batch mode, fetched later for top candidates
        catalyst = ""
        if fetch_catalyst and _tavily:
            try:
                r = _tavily.search(
                    f"{ticker} catalyst event earnings partnership news week 2026",
                    max_results=2,
                )
                catalyst = " ".join(
                    (x.get("content") or "")[:120] for x in (r.get("results") or [])
                )[:250]
            except Exception:
                pass

        narrative_sector = _NARRATIVE_TICKERS.get(ticker)

        # Identify source universe
        source = "S&P500/NDX"
        if ticker in WATCHLIST:
            source = "Watchlist"
        if narrative_sector:
            source = f"{source} ({narrative_sector})"

        candidate = {
            "ticker":           ticker,
            "price":            round(price, 2),
            "rsi":              rsi,
            "pattern":          pattern,
            "rr":               rr,
            "tech":             tech,
            "atr_pct":          atr_pct,
            "potential_pct":    potential_pct,
            "avg_dv_m":         round(avg_dv_m, 1),
            "vol_spike":        vol_spike,
            "rs_vs_qqq":        rs_vs_qqq,
            "ma50_slope_up":    slope_up,
            "above_ma200":      bool(tech.get("above_ma200")),
            "pct_from_ma50":    pattern.get("pct_from_ma50", 0),
            "catalyst":         catalyst,
            "narrative_sector": narrative_sector,
            "source":           source,
        }
        candidate["score"] = _calc_swing_score(candidate)
        return candidate

    except Exception as e:
        logger.warning("Setup analysis error %s: %s", ticker, e)
        return None


def _analyze_setup_coin(coin: dict, btc_dominance: float | None) -> dict | None:
    """Setup analysis for a CoinGecko coin. Anti-pump: >20% in 7d = skip."""
    chg7d = coin.get("price_change_percentage_7d_in_currency") or 0
    if chg7d > 20:
        return None
    chg24   = coin.get("price_change_percentage_24h") or 0
    ath     = coin.get("ath") or 0
    price   = coin.get("current_price") or 0
    pct_ath = round((price - ath) / ath * 100, 1) if ath else None
    rank    = coin.get("market_cap_rank", 99)

    if not (-5 <= chg7d <= 18):
        return None
    if pct_ath is not None and pct_ath > -8:
        return None

    catalyst = ""
    if _tavily:
        try:
            sym = (coin.get("symbol") or "").upper()
            r   = _tavily.search(f"{sym} crypto catalyst upcoming week 2026", max_results=1)
            catalyst = " ".join(
                (x.get("content") or "")[:120] for x in (r.get("results") or [])
            )[:200]
        except Exception:
            pass

    score = 30  # base crypto score (higher volatility = higher potential)
    if btc_dominance and btc_dominance < 50: score += 10  # alt season
    if catalyst:  score += 15
    if rank <= 10: score += 10
    if rank <= 3:  score += 10

    return {
        "ticker":    (coin.get("symbol") or "?").upper(),
        "name":      coin.get("name", ""),
        "price":     price,
        "chg7d":     chg7d,
        "chg24":     chg24,
        "pct_ath":   pct_ath,
        "rank":      rank,
        "catalyst":  catalyst,
        "score":     score,
        "source":    f"CoinGecko #{rank}",
        "is_crypto": True,
    }


# ── Claude picks top setups ───────────────────────────────────────────────────

_SWING_SYSTEM = (
    "Jesteś doświadczonym swing traderem. Wybierasz TOP zagrań tygodniowych.\n"
    "PRIORYTET: spółki z potencjałem ruchu >10% w ciągu 5-10 sesji.\n"
    "Wymagania: RSI 40-70, powyżej MA50, wyraźny pattern, R/R ≥ 2:1, płynność ≥ $5M/dzień.\n"
    "Dla krypto: nie wchodzisz po pompie >20% w 7d.\n"
    "ODRZUĆ: fake breakout (low volume), earnings w 3 dni (ryzyko), score < 40.\n"
    "WAŻNE: Makro (RISK-OFF/ON) to tylko kontekst — ZAWSZE zwróć dokładnie tyle setupów ile prosi użytkownik.\n"
    "Wybierz NAJLEPSZYCH z dostępnych kandydatów, nawet jeśli rynek jest niepewny.\n"
    "ODPOWIADASZ TYLKO W JSON (tablica):\n"
    '[{"ticker":"...","pattern":"...","entry":0.0,"target":0.0,"stop":0.0,'
    '"target_pct":0.0,"stop_pct":0.0,"rr":0.0,'
    '"window_days":7,"catalyst":"...","reason":"1 zdanie po polsku — uwzględnij potencjał %"}]'
)


def _pick_top_setups(candidates: list[dict], macro: dict, limit: int = 5) -> list[dict]:
    if not candidates:
        return []

    # Sort by score, take top 20 for Claude
    top20 = sorted(candidates, key=lambda x: x.get("score", 0), reverse=True)[:20]

    lines = []
    for c in top20:
        if c.get("is_crypto"):
            lines.append(
                f"{c['ticker']} [CRYPTO rank#{c['rank']}]: ${c['price']} | "
                f"7d={c['chg7d']:+.1f}% | odATH={c['pct_ath']}% | "
                f"Score={c['score']} | Catalyst: {c['catalyst'][:80] or 'brak'}"
            )
        else:
            rr  = c.get("rr", {})
            pat = c.get("pattern", {})
            lines.append(
                f"{c['ticker']} [{c.get('source','')}]: ${c['price']} | "
                f"RSI={c['rsi']} | Score={c['score']}/100 | "
                f"Pattern={pat.get('pattern','')} | "
                f"Potencjał={c.get('potential_pct','?')}% (ATR×3) | "
                f"R/R={rr.get('rr_ratio')} | VolSpike={c.get('vol_spike','?')}x | "
                f"RS_vs_QQQ={c.get('rs_vs_qqq','?')}% | "
                f"Catalyst: {c['catalyst'][:80] or 'brak'}"
            )

    top_n = max(3, min(limit, 15))
    prompt = (
        f"Makro: {macro.get('sentiment','?')} — {macro.get('main_risk','')}\n\n"
        "Kandydaci z pełnego skanu rynku (posortowani po score):\n"
        + "\n".join(lines)
        + f"\n\nWybierz DOKŁADNIE {top_n} najlepszych zagrań i zwróć JSON. "
        "Jeśli jest mniej kandydatów — zwróć tylu ilu jest."
    )

    claude_setups: list[dict] = []
    try:
        resp = _ctx.claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            system=_SWING_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        m   = re.search(r"\[.*\]", raw, re.DOTALL)
        claude_setups = json.loads(m.group() if m else "[]")
    except Exception as e:
        logger.warning("Pick top setups error: %s", e)

    # Fallback: if Claude returned fewer than requested, supplement with
    # top-scored candidates directly so we always hit the target count.
    if len(claude_setups) < top_n:
        picked_tickers = {s.get("ticker") for s in claude_setups}
        for c in top20:
            if len(claude_setups) >= top_n:
                break
            if c.get("ticker") in picked_tickers:
                continue
            rr = c.get("rr", {})
            pat = c.get("pattern", {})
            claude_setups.append({
                "ticker":      c["ticker"],
                "pattern":     pat.get("pattern", "Technical setup"),
                "entry":       rr.get("entry", c.get("price", 0)),
                "target":      rr.get("target", 0),
                "stop":        rr.get("stop", 0),
                "target_pct":  c.get("potential_pct", rr.get("target_pct", 0)),
                "stop_pct":    rr.get("stop_pct", 0),
                "rr":          rr.get("rr_ratio", 0),
                "window_days": 7,
                "catalyst":    c.get("catalyst", ""),
                "reason":      f"Score {c.get('score',0)}/100 — {pat.get('pattern','')}",
            })
            picked_tickers.add(c["ticker"])

    return claude_setups


# ── Formatting ────────────────────────────────────────────────────────────────

def _score_emoji(score: int) -> str:
    if score >= 70: return "🟢"
    if score >= 50: return "🟡"
    return "🔴"


def _format_setup_attachment(i: int, s: dict, candidate_data: dict | None = None) -> dict:
    color  = "#2eb886"
    ticker = s.get("ticker", "?")
    rr_val = s.get("rr", "?")

    source          = (candidate_data or {}).get("source", "")
    potential_pct   = (candidate_data or {}).get("potential_pct", s.get("target_pct"))
    score           = (candidate_data or {}).get("score", 0)
    narrative_sector = (candidate_data or {}).get("narrative_sector")
    vol_spike       = (candidate_data or {}).get("vol_spike")

    header_text = f"*{i}. {ticker}* — _{s.get('pattern','?')}_"
    if narrative_sector:
        header_text += f"  🔥 _{narrative_sector}_"

    fields = [
        {"type": "mrkdwn", "text": f"*Wejście*\n${s.get('entry','?')}"},
        {"type": "mrkdwn", "text": f"*Cel (+{s.get('target_pct','?')}%)*\n${s.get('target','?')}"},
        {"type": "mrkdwn", "text": f"*Stop*\n${s.get('stop','?')} ({s.get('stop_pct','?')}%)"},
        {"type": "mrkdwn", "text": f"*R/R*\n{rr_val}:1"},
        {"type": "mrkdwn", "text": f"*Potencjał*\n~{potential_pct}%"},
        {"type": "mrkdwn", "text": f"*Okno*\n{s.get('window_days',7)} dni"},
    ]

    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": header_text}, "fields": fields}
    ]

    meta_parts = []
    if source:       meta_parts.append(f"Skąd: {source}")
    if score:        meta_parts.append(f"Score: {_score_emoji(score)} {score}/100")
    if vol_spike and vol_spike >= 1.2:
        meta_parts.append(f"Vol spike: {vol_spike}×")
    if s.get("catalyst"):
        meta_parts.append(f"Katalizator: {s['catalyst'][:100]}")
    if meta_parts:
        blocks.append({"type": "context",
                        "elements": [{"type": "mrkdwn", "text": "  ·  ".join(meta_parts)}]})

    if s.get("reason"):
        blocks.append({"type": "context",
                        "elements": [{"type": "mrkdwn", "text": f"_{s['reason']}_"}]})
    return {"color": color, "blocks": blocks}


# ── Candidate collection (all modes) ─────────────────────────────────────────

def _scan_candidates(mode: str = "all") -> tuple[list[dict], dict, float | None]:
    """
    Collect all passing setup candidates.
    mode: 'all' | 'watchlist' | sector name (e.g. 'space')
    Returns (candidates, macro, btc_dom).
    """
    from jobs.stock_digest import _fetch_qqq_30d
    macro   = fetch_macro_briefing()
    btc_dom = _fetch_btc_dominance()
    qqq_30d = _fetch_qqq_30d()

    universe = _build_scan_universe(mode)
    logger.info("Swing scan: mode=%s, universe=%d tickers", mode, len(universe))

    candidates: list[dict] = []

    if mode == "all" and len(universe) > len(WATCHLIST) + 10:
        # ── Large universe: batch pre-screen → parallel full analysis ──────────
        logger.info("Batch pre-screening %d tickers...", len(universe))
        prescreened = _batch_prescreen(universe, qqq_30d=qqq_30d)

        # Sort by momentum + volume spike, keep top 30 for full analysis
        prescreened.sort(
            key=lambda x: x.get("rs_vs_qqq", 0) + x.get("vol_spike", 1) * 5,
            reverse=True,
        )
        top_prescreened = prescreened[:30]
        logger.info("Pre-screen: %d → %d for full analysis", len(universe), len(top_prescreened))

        # Batch-download 1y history for all top candidates in ONE call
        # This avoids 30 individual t.history() calls in the parallel phase
        top_tickers = [p["ticker"] for p in top_prescreened]
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                bulk_hist = yf.download(
                    top_tickers, period="1y", progress=False,
                    auto_adjust=True, timeout=30,
                )
        except Exception as e:
            logger.warning("Bulk history download failed: %s", e)
            bulk_hist = None

        if bulk_hist is not None and not bulk_hist.empty and isinstance(bulk_hist.columns, pd.MultiIndex):
            for p in top_prescreened:
                try:
                    t_hist = bulk_hist.xs(p["ticker"], axis=1, level=1).dropna(how="all")
                    if len(t_hist) >= 22:
                        p["_hist"] = t_hist
                except Exception:
                    pass

        # Parallel full analysis — no Tavily yet (fetch_catalyst=False)
        # max_workers=4 to avoid Yahoo Finance rate limiting
        def _full_no_catalyst(p):
            try:
                return _analyze_setup_ticker(p["ticker"], prescreen=p, fetch_catalyst=False)
            except Exception as e:
                logger.warning("Parallel analysis error %s: %s", p["ticker"], e)
                return None

        from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_full_no_catalyst, p): p["ticker"] for p in top_prescreened}
            for fut in as_completed(futures, timeout=120):
                try:
                    result = fut.result(timeout=20)
                    if result:
                        candidates.append(result)
                except FuturesTimeout:
                    logger.warning("Ticker %s timed out (20s)", futures[fut])
                except Exception as e:
                    logger.warning("Ticker %s future error: %s", futures[fut], e)

        # Fetch catalyst only for top 10 by score (sequential, after parallel analysis)
        if _tavily and candidates:
            candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
            for c in candidates[:10]:
                try:
                    r = _tavily.search(
                        f"{c['ticker']} catalyst event earnings partnership news week 2026",
                        max_results=2,
                    )
                    c["catalyst"] = " ".join(
                        (x.get("content") or "")[:120] for x in (r.get("results") or [])
                    )[:250]
                    if c["catalyst"]:
                        c["score"] = min(100, c["score"] + 15)
                except Exception:
                    pass
    else:
        # Small universe (watchlist or sector) — sequential with catalyst
        for ticker in universe:
            s = _analyze_setup_ticker(ticker, fetch_catalyst=True)
            if s:
                candidates.append(s)

    # Crypto (always top 100)
    coins = _fetch_top_coins(100)
    for coin in coins:
        s = _analyze_setup_coin(coin, btc_dom)
        if s:
            candidates.append(s)

    logger.info("Scan complete: %d total candidates", len(candidates))
    return candidates, macro, btc_dom


# ── Public entry points ───────────────────────────────────────────────────────

def send_weekly_setups(limit: int = 5, mode: str = "all"):
    """Post weekly swing setups to #inwestowanie."""
    try:
        today  = datetime.datetime.now().strftime("%d.%m.%Y")
        end_dt = (datetime.datetime.now() + datetime.timedelta(days=5)).strftime("%d.%m.%Y")

        candidates, macro, btc_dom = _scan_candidates(mode)

        s_emoji = {"RISK-ON": "🟢", "RISK-OFF": "🔴", "NEUTRALNY": "🟡"}.get(
            macro.get("sentiment", ""), "🟡"
        )
        dom_str = f"{btc_dom}%" if btc_dom is not None else "N/A"

        stock_c  = [c for c in candidates if not c.get("is_crypto")]
        crypto_c = [c for c in candidates if c.get("is_crypto")]

        mode_label = {
            "all":       "S&P500 + NDX + watchlista",
            "watchlist": "watchlista",
        }.get(mode, mode)

        header = (
            f"🎯 *Weekly Setups — {today} → {end_dt}*\n"
            f"{s_emoji} Makro: {macro.get('sentiment','?')}  |  "
            f"₿ Dominance: {dom_str}  |  "
            f"Skan: _{mode_label}_\n"
            f"📊 Kandydaci: {len(stock_c)} spółek + {len(crypto_c)} krypto\n"
            f"_{macro.get('main_risk','')}_"
        )
        _ctx.app.client.chat_postMessage(channel=STOCK_CHANNEL_ID, text=header)

        setups = _pick_top_setups(candidates, macro, limit=limit)

        if not setups:
            _ctx.app.client.chat_postMessage(
                channel=STOCK_CHANNEL_ID,
                text=(
                    f"📭 Brak wyraźnych setupów w tym tygodniu "
                    f"({len(candidates)} kandydatów, żaden nie wygrał selekcji Claude).\n"
                    f"Rynek może być zbyt zmienny lub wszystkie spółki poza optymalną strefą."
                ),
            )
            return

        # Map tickers back to full candidate data for display
        candidate_map = {c.get("ticker"): c for c in candidates}

        attachments = [
            _format_setup_attachment(i + 1, s, candidate_map.get(s.get("ticker")))
            for i, s in enumerate(setups)
        ]
        _ctx.app.client.chat_postMessage(
            channel=STOCK_CHANNEL_ID,
            text=f"🎯 *TOP {len(setups)} zagrań — {today} → {end_dt}*",
            attachments=attachments,
        )

        # Highlight off-watchlist discoveries
        new_finds = [
            s.get("ticker") for s in setups
            if s.get("ticker") not in WATCHLIST
            and not candidate_map.get(s.get("ticker"), {}).get("is_crypto")
        ]
        if new_finds:
            _ctx.app.client.chat_postMessage(
                channel=STOCK_CHANNEL_ID,
                text=f"🔍 *Spoza twojej watchlisty:* {', '.join(new_finds)}",
            )

        _ctx.app.client.chat_postMessage(
            channel=STOCK_CHANNEL_ID,
            text=(
                "⚠️ _Setupy techniczne z danych historycznych. "
                "Zawsze ustaw stop-loss. Nie inwestuj więcej niż możesz stracić._"
            ),
        )
        logger.info("send_weekly_setups: done, %d setups (mode=%s)", len(setups), mode)
    except Exception as e:
        logger.error("send_weekly_setups failed: %s", e)


def send_scan_setups(mode: str = "all"):
    """Post ALL passing candidates sorted by score — no Claude filter."""
    try:
        today      = datetime.datetime.now().strftime("%d.%m.%Y")
        candidates, macro, btc_dom = _scan_candidates(mode)

        s_emoji = {"RISK-ON": "🟢", "RISK-OFF": "🔴", "NEUTRALNY": "🟡"}.get(
            macro.get("sentiment", ""), "🟡"
        )
        dom_str = f"{btc_dom}%" if btc_dom is not None else "N/A"

        if not candidates:
            _ctx.app.client.chat_postMessage(
                channel=STOCK_CHANNEL_ID,
                text="📭 Scan nie znalazł żadnych kandydatów (RSI 40-70, MA50, potencjał ≥8%).",
            )
            return

        sorted_c = sorted(candidates, key=lambda x: x.get("score", 0), reverse=True)
        stocks   = [c for c in sorted_c if not c.get("is_crypto")]
        cryptos  = [c for c in sorted_c if c.get("is_crypto")]

        def _stock_line(i, c):
            rr  = c.get("rr", {})
            pat = c.get("pattern", {})
            em  = _score_emoji(c.get("score", 0))
            narr = f" 🔥{c['narrative_sector']}" if c.get("narrative_sector") else ""
            return (
                f"{em} *{i}. {c['ticker']}*{narr}  RSI:{c['rsi']}  "
                f"Score:{c['score']}/100  "
                f"_{pat.get('pattern','?')}_  "
                f"Cel:+{rr.get('target_pct','?')}%  "
                f"R/R:{rr.get('rr_ratio','?')}:1  "
                f"Potencjał:~{c.get('potential_pct','?')}%  "
                f"VolSpike:{c.get('vol_spike','?')}×"
            )

        def _crypto_line(i, c):
            em = _score_emoji(c.get("score", 0))
            return (
                f"{em} *{i}. {c['ticker']}*  #{c['rank']}  "
                f"${c['price']}  7d:{c['chg7d']:+.1f}%  "
                f"odATH:{c['pct_ath']}%  Score:{c['score']}"
            )

        stock_lines  = [_stock_line(i + 1, c) for i, c in enumerate(stocks)]
        crypto_lines = [_crypto_line(i + 1, c) for i, c in enumerate(cryptos)]

        mode_label = {
            "all":       "S&P500 + NDX + watchlista",
            "watchlist": "watchlista",
        }.get(mode, mode)

        header = (
            f"🔍 *Swing Scan — {today}*  |  Skan: _{mode_label}_\n"
            f"{s_emoji} Makro: {macro.get('sentiment','?')}  |  "
            f"₿ Dominance: {dom_str}  |  "
            f"{len(stocks)} spółek + {len(cryptos)} krypto\n"
            f"🟢 score≥70  🟡 score 50-69  🔴 score<50  |  "
            f"_`/swing TICKER` po szczegóły_"
        )
        _ctx.app.client.chat_postMessage(channel=STOCK_CHANNEL_ID, text=header)

        if stock_lines:
            _ctx.app.client.chat_postMessage(
                channel=STOCK_CHANNEL_ID,
                text="*📈 Spółki (po score):*\n" + "\n".join(stock_lines),
            )
        if crypto_lines:
            _ctx.app.client.chat_postMessage(
                channel=STOCK_CHANNEL_ID,
                text="*₿ Krypto:*\n" + "\n".join(crypto_lines),
            )

        _ctx.app.client.chat_postMessage(
            channel=STOCK_CHANNEL_ID,
            text="⚠️ _Scan surowy — brak oceny Claude. Nie inwestuj więcej niż możesz stracić._",
        )
        logger.info("send_scan_setups: done, %d candidates (mode=%s)", len(sorted_c), mode)
    except Exception as e:
        logger.error("send_scan_setups failed: %s", e)


# ── Deep single-ticker analysis ───────────────────────────────────────────────

_DEEP_SWING_SYSTEM = (
    "Jesteś doświadczonym swing traderem i analitykiem rynkowym. "
    "Piszesz po polsku, zwięźle ale konkretnie. "
    "Twoja analiza ma pomóc zdecydować: czy wchodzić w to zagranie TERAZ, CZEKAĆ, czy je OMIJAĆ. "
    "Format odpowiedzi (Slack mrkdwn):\n"
    "1. *Kontekst makro* — jak makro wpływa na ten ticker\n"
    "2. *Setup techniczny* — dlaczego ten pattern jest lub nie jest przekonujący\n"
    "3. *Potencjał ruchu* — czy możliwe >10%? Na podstawie ATR i historycznej zmienności\n"
    "4. *Katalizatory* — co może ruszyć cenę w ciągu 1-2 tygodni\n"
    "5. *Ryzyka* — co może zniszczyć setup\n"
    "6. *Werdykt* — WCHODZĘ / CZEKAM / OMIJAM + 1 zdanie uzasadnienia\n"
    "Maksymalnie 280 słów. Pisz jak do kolegi tradera."
)


def analyze_single_swing(ticker: str) -> str:
    """Deep swing analysis for one ticker. Returns formatted Slack text."""
    ticker = ticker.upper().strip()

    # ── Crypto path ──────────────────────────────────────────────────────────
    coins = _fetch_top_coins(100)
    coin_data = next((c for c in coins if (c.get("symbol") or "").upper() == ticker), None)

    if coin_data:
        btc_dom = _fetch_btc_dominance()
        macro   = fetch_macro_briefing()
        chg7d   = coin_data.get("price_change_percentage_7d_in_currency") or 0
        price   = coin_data.get("current_price", 0)
        name    = coin_data.get("name", ticker)
        rank    = coin_data.get("market_cap_rank", "?")
        pct_ath = round((price / coin_data["ath"] - 1) * 100, 1) if coin_data.get("ath") else "?"

        if chg7d > 20:
            return f"🚫 *{ticker}* — anty-pump: wzrost +{chg7d:.0f}% w 7d. Poczekaj na cofkę."

        news = ""
        if _tavily:
            try:
                r = _tavily.search(f"{name} {ticker} crypto price catalyst news 2026", max_results=3)
                news = " | ".join(
                    (x.get("content") or "")[:150] for x in (r.get("results") or [])
                )[:400]
            except Exception:
                pass

        dom_str  = f"{btc_dom}%" if btc_dom is not None else "N/A"
        user_msg = (
            f"Ticker: {ticker} ({name})  Rank: #{rank}\n"
            f"Cena: ${price}  |  7d: {chg7d:+.1f}%  |  od ATH: {pct_ath}%\n"
            f"BTC Dominance: {dom_str}\n"
            f"Makro: {macro.get('sentiment','?')} — {macro.get('summary','')[:200]}\n"
            f"Główne ryzyko makro: {macro.get('main_risk','')}\n"
            f"Newsy/katalizatory: {news or 'brak'}\n"
        )

        try:
            resp = _ctx.claude.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=600,
                system=_DEEP_SWING_SYSTEM,
                messages=[{"role": "user", "content": user_msg}],
            )
            analysis = resp.content[0].text.strip()
        except Exception as e:
            analysis = f"_(błąd Claude: {e})_"

        header = (
            f"🎯 *{ticker}* ({name})  #{rank}  "
            f"${price}  7d:{chg7d:+.1f}%  odATH:{pct_ath}%"
        )
        return f"{header}\n\n{analysis}"

    # ── Stock path ────────────────────────────────────────────────────────────
    try:
        t_obj = yf.Ticker(ticker)
        hist  = t_obj.history(period="6mo", interval="1d")
    except Exception:
        hist = None

    if hist is None or hist.empty:
        return f"❌ *{ticker}* — nie znaleziono danych. Sprawdź symbol."

    closes = hist["Close"].tolist()
    if len(closes) < 22:
        return f"❌ *{ticker}* — za mało danych historycznych."

    rsi      = round(_calc_rsi(closes), 1)
    tech     = _calc_technicals(closes)
    pattern  = _detect_pattern(closes, ticker)
    atr_pct  = _calc_atr_pct(hist)
    rr       = _calc_rr(closes, atr_pct=atr_pct)
    price    = round(closes[-1], 2)
    chg1m    = round((closes[-1] / closes[-22] - 1) * 100, 1) if len(closes) >= 22 else "?"
    chg3m    = round((closes[-1] / closes[0] - 1) * 100, 1)
    potential_pct = round(atr_pct * 3, 1)

    macro = fetch_macro_briefing()

    news = ""
    if _tavily:
        try:
            r = _tavily.search(
                f"{ticker} stock earnings catalyst news week 2026", max_results=4
            )
            news = " | ".join(
                (x.get("content") or "")[:180] for x in (r.get("results") or [])
            )[:500]
        except Exception:
            pass

    above_ma50  = "✅" if tech.get("above_ma50")  else "❌"
    above_ma200 = "✅" if tech.get("above_ma200") else "❌"
    golden = ("🟡 Golden cross" if tech.get("golden_cross")
              else ("💀 Death cross" if tech.get("death_cross") else "brak"))

    user_msg = (
        f"Ticker: {ticker}\n"
        f"Cena: ${price}  |  1m: {chg1m:+}%  |  3m: {chg3m:+}%\n"
        f"RSI-14: {rsi}  |  MA50: {above_ma50}  |  MA200: {above_ma200}  |  {golden}\n"
        f"ATR(14): {atr_pct}% ceny  |  Potencjał ruchu (ATR×3): ~{potential_pct}%\n"
        f"Pattern: {pattern.get('pattern','?')}  (quality={pattern.get('quality',0)})\n"
        f"Wejście: ${rr.get('entry','?')}  Cel: ${rr.get('target','?')} (+{rr.get('target_pct','?')}%)  "
        f"Stop: ${rr.get('stop','?')} ({rr.get('stop_pct','?')}%)  R/R: {rr.get('rr_ratio','?')}:1\n"
        f"Makro: {macro.get('sentiment','?')} — {macro.get('summary','')[:200]}\n"
        f"Główne ryzyko makro: {macro.get('main_risk','')}\n"
        f"Newsy/katalizatory: {news or 'brak'}\n"
    )

    try:
        resp = _ctx.claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=600,
            system=_DEEP_SWING_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        analysis = resp.content[0].text.strip()
    except Exception as e:
        analysis = f"_(błąd Claude: {e})_"

    header = (
        f"🎯 *{ticker}*  ${price}  "
        f"RSI:{rsi}  MA50:{above_ma50}  MA200:{above_ma200}\n"
        f"_{pattern.get('pattern','?')}_  |  "
        f"ATR×3: ~{potential_pct}%  |  "
        f"Wejście:${rr.get('entry','?')}  Cel:+{rr.get('target_pct','?')}%  "
        f"Stop:{rr.get('stop_pct','?')}%  R/R:{rr.get('rr_ratio','?')}:1"
    )
    return f"{header}\n\n{analysis}"
