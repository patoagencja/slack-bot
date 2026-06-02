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


# ── Minervini Trend Template ──────────────────────────────────────────────────

def _minervini_template(closes: list) -> tuple[int, dict]:
    """
    Minervini Trend Template — 7 criteria, 1 pt each.
    Returns (score 0-7, criteria dict).
    """
    if len(closes) < 150:
        return 0, {}
    try:
        s     = pd.Series(closes)
        price = closes[-1]
        ma50  = float(s.rolling(50).mean().iloc[-1])
        ma150 = float(s.rolling(150).mean().iloc[-1])
        ma200 = float(s.rolling(200).mean().iloc[-1]) if len(closes) >= 200 else None
        ma200_ago = float(s.rolling(200).mean().iloc[-21]) if len(closes) >= 221 else None
        high52 = max(closes[-252:]) if len(closes) >= 252 else max(closes)
        low52  = min(closes[-252:]) if len(closes) >= 252 else min(closes)
        criteria = {
            "c1": price > ma150 and (ma200 is None or price > ma200),
            "c2": ma200 is None or ma150 > ma200,
            "c3": (ma200_ago is not None and ma200 is not None and ma200 > ma200_ago),
            "c4": ma200 is None or (ma50 > ma150 and ma50 > ma200),
            "c5": price > ma50,
            "c6": price >= high52 * 0.75,   # within 25% of 52w high
            "c7": price >= low52 * 1.30,    # at least 30% above 52w low
        }
        score = sum(1 for v in criteria.values() if v)
        return score, criteria
    except Exception:
        return 0, {}


# ── Weinstein Stage Analysis ──────────────────────────────────────────────────

def _weinstein_stage(hist: "pd.DataFrame") -> int:
    """
    Weinstein Stage 1-4 derived from daily history resampled to weekly.
    Stage 2 (advancing) = only stage worth buying.
    """
    if hist is None or len(hist) < 50:
        return 0
    try:
        weekly = hist["Close"].resample("W").last().dropna()
        if len(weekly) < 10:
            return 0
        n = min(30, len(weekly))
        ma_w = weekly.rolling(n).mean()
        if pd.isna(ma_w.iloc[-1]):
            return 0
        price_w  = float(weekly.iloc[-1])
        ma_now   = float(ma_w.iloc[-1])
        ma_prev  = float(ma_w.iloc[-min(5, len(ma_w)-1)])
        above_ma = price_w > ma_now
        rising   = ma_now > ma_prev
        # Higher highs + higher lows check (last 8 weeks)
        recent = weekly.tail(8).tolist()
        if len(recent) >= 4:
            mid = len(recent) // 2
            higher_highs = max(recent[mid:]) > max(recent[:mid])
            higher_lows  = min(recent[mid:]) > min(recent[:mid])
        else:
            higher_highs = higher_lows = False
        if above_ma and rising and (higher_highs or higher_lows):
            return 2
        elif above_ma and not rising:
            return 3
        elif not above_ma and not rising:
            return 4
        else:
            return 1
    except Exception:
        return 0


# ── VCP Pattern (Minervini) ───────────────────────────────────────────────────

def _vcp_detect(closes: list, volumes: list | None = None) -> bool:
    """
    Volatility Contraction Pattern: 3 pullbacks with shrinking depth and volume.
    """
    if len(closes) < 60:
        return False
    try:
        recent = closes[-60:]
        pullbacks: list[float] = []
        i = 2
        while i < len(recent) - 2:
            if recent[i] > recent[i-1] and recent[i] > recent[i+1]:
                # find trough after this peak
                j = i + 1
                while j < len(recent) - 1 and recent[j] > recent[j-1]:
                    j += 1
                trough = min(recent[i:min(j+3, len(recent))])
                depth  = (recent[i] - trough) / recent[i] * 100
                pullbacks.append(depth)
                i = j + 1
            else:
                i += 1
        if len(pullbacks) < 3:
            return False
        d1, d2, d3 = pullbacks[-3], pullbacks[-2], pullbacks[-1]
        if not (d1 > d2 > d3 and d1 >= 10 and 2 <= d3 <= 8):
            return False
        # Volume confirmation: recent 5d below 20d average
        if volumes and len(volumes) >= 25:
            avg_vol = sum(volumes[-25:-5]) / 20
            rec_vol = sum(volumes[-5:]) / 5
            if rec_vol > avg_vol * 1.1:
                return False
        return True
    except Exception:
        return False


# ── Wyckoff Spring ────────────────────────────────────────────────────────────

def _wyckoff_spring(closes: list, volumes: list | None = None) -> bool:
    """
    Wyckoff Spring: false breakdown below support + quick recovery on low volume.
    """
    if len(closes) < 30:
        return False
    try:
        recent  = closes[-30:]
        support = min(recent[5:25])
        # Dip below support in days -10..-4, then recovered
        dipped    = any(p < support * 0.985 for p in recent[18:26])
        recovered = recent[-1] > support * 1.01
        if not (dipped and recovered):
            return False
        if volumes and len(volumes) >= 30:
            avg_vol = sum(volumes[-30:-10]) / 20
            dip_vol = sum(volumes[-10:-3]) / 7
            if dip_vol > avg_vol * 0.95:
                return False
        return True
    except Exception:
        return False


# ── Anchored VWAP Support ─────────────────────────────────────────────────────

def _avwap_support(hist: "pd.DataFrame") -> bool:
    """
    AVWAP anchored at 52-week low. Price within 4% above AVWAP = institutional support.
    """
    if hist is None or len(hist) < 30:
        return False
    try:
        closes  = hist["Close"]
        volumes = hist["Volume"]
        anchor_idx = closes.tail(252).idxmin()
        sub = hist.loc[anchor_idx:]
        if len(sub) < 5:
            return False
        cumvp  = (sub["Close"] * sub["Volume"]).cumsum()
        cumv   = sub["Volume"].cumsum()
        if float(cumv.iloc[-1]) == 0:
            return False
        avwap = float(cumvp.iloc[-1] / cumv.iloc[-1])
        price = float(closes.iloc[-1])
        pct   = (price - avwap) / avwap * 100
        return 0 <= pct <= 4
    except Exception:
        return False


# ── Multi-timeframe Confirmation ──────────────────────────────────────────────

def _multi_tf_confirmation(closes: list, hist: "pd.DataFrame") -> int:
    """
    1 pt per timeframe confirmed: daily (base), weekly trend, short-term (4H proxy).
    Returns 1-3.
    """
    score = 1  # daily is confirmed by the time we reach here
    try:
        weekly = hist["Close"].resample("W").last().dropna()
        if len(weekly) >= 10:
            n  = min(30, len(weekly))
            maw = weekly.rolling(n).mean()
            if (not pd.isna(maw.iloc[-1]) and float(weekly.iloc[-1]) > float(maw.iloc[-1])
                    and float(maw.iloc[-1]) > float(maw.iloc[-min(4, len(maw)-1)])):
                score += 1
    except Exception:
        pass
    # Short-term: last 3 closes above 10-day MA
    if len(closes) >= 12:
        ma10 = sum(closes[-10:]) / 10
        if closes[-1] > ma10 and closes[-3] > ma10:
            score += 1
    return min(3, score)


# ── CAN SLIM Score (simplified — no earnings data needed) ─────────────────────

def _canslim_score(closes: list, rs_rank: int = 50, vol_spike: float = 1.0,
                   avwap: bool = False, macro_risk_on: bool = False) -> int:
    """
    Simplified CAN SLIM (0-7). C and A skipped (require earnings).
    N, S, L, I, M from available price/volume data.
    """
    score = 0
    if len(closes) < 60:
        return score
    high52 = max(closes[-252:]) if len(closes) >= 252 else max(closes)
    low52  = min(closes[-252:]) if len(closes) >= 252 else min(closes)
    price  = closes[-1]
    rng    = high52 - low52
    pos_in_range = (price - low52) / rng if rng else 0.5
    pct_from_high = (price / high52 - 1) * 100

    # N — price in top 25% of 52w range but not within 5% of ATH
    if pos_in_range > 0.75 and pct_from_high < -5:
        score += 1
    # S — supply/demand: volume accumulation
    if vol_spike >= 1.3:
        score += 1
    # L — leader: RS rank top 20%
    if rs_rank >= 80:
        score += 2
    elif rs_rank >= 70:
        score += 1
    # I — institutional support proxy: AVWAP + tight base
    if avwap:
        score += 1
    recent = closes[-20:]
    tight  = (max(recent) - min(recent)) / min(recent) * 100 < 8 if min(recent) else False
    if tight:
        score += 1
    # M — market in uptrend
    if macro_risk_on:
        score += 1
    return min(7, score)


# ── Final Setup Grade (0-28) ──────────────────────────────────────────────────

def _calc_setup_grade(minervini: int, weinstein: int, canslim: int,
                      vcp: bool, spring: bool, multi_tf: int,
                      avwap: bool, rr_ratio: float) -> tuple[str, int]:
    """Returns (grade A+/B/C/D, score 0-28)."""
    score  = minervini                          # 0-7
    score += 3 if weinstein == 2 else 0         # Weinstein Stage 2
    score += canslim                            # 0-7
    score += 3 if (vcp or spring) else 0        # pattern premium
    score += min(3, multi_tf)                   # 1-3
    score += 2 if avwap else 0                  # AVWAP support
    if rr_ratio >= 3:    score += 3
    elif rr_ratio >= 2:  score += 2
    grade = "A+" if score >= 24 else "B" if score >= 18 else "C" if score >= 12 else "D"
    return grade, score



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
                        chunk, period="1y", progress=False,
                        auto_adjust=True, timeout=45,
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

                # Trend: price above MA50 OR pulling back to MA50 (within -5%)
                # Relaxed to catch MA50 bounce setups in RISK-OFF conditions
                ma50 = float(c.rolling(50).mean().iloc[-1]) if len(c) >= 50 else float(c.mean())
                pct_vs_ma50 = (price / ma50 - 1) * 100 if ma50 else 0
                if pct_vs_ma50 < -5:
                    continue

                # ATH filter: skip stocks within 5% of 52-week high (early reject)
                high52 = float(c.tail(252).max()) if len(c) >= 252 else float(c.max())
                if (price / high52 - 1) * 100 > -5:
                    continue

                # RSI filter — widened to 35-73 to catch more setups in choppy markets
                rsi = round(_calc_rsi(c.tolist()), 1)
                if not (35 <= rsi <= 73):
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
                r5d  = round((price / float(c.iloc[-5])  - 1) * 100, 2) if len(c) >= 5  else 0.0
                r20d = round((price / float(c.iloc[-20]) - 1) * 100, 2) if len(c) >= 20 else 0.0
                momentum_30d = round((price / float(c.iloc[-30]) - 1) * 100, 2) if len(c) >= 30 else 0.0
                rs_vs_qqq = round(momentum_30d - qqq_30d, 2) if qqq_30d is not None else 0.0
                if rs_vs_qqq < -10:
                    continue  # badly lagging market

                candidates.append({
                    "ticker":       t,
                    "price":        round(price, 2),
                    "rsi":          rsi,
                    "r5d":          r5d,
                    "r20d":         r20d,
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
    """Score 0-100 combining technical momentum + Minervini/Weinstein/pattern quality."""
    score = 0

    # Trend (25 pts)
    if c.get("ma50_slope_up"):   score += 8
    if c.get("above_ma200"):     score += 8
    pct50 = c.get("pct_from_ma50", 99)
    if 0 < pct50 < 5:            score += 9
    elif 5 <= pct50 < 12:        score += 4

    # Momentum (20 pts)
    rs = c.get("rs_vs_qqq", 0)
    if rs > 10:    score += 12
    elif rs > 2:   score += 8
    elif rs > -3:  score += 3
    vs = c.get("vol_spike", 1.0)
    if vs >= 1.5:  score += 8
    elif vs >= 1.2: score += 4

    # RSI (15 pts)
    rsi = c.get("rsi", 50)
    if 50 <= rsi <= 65:                         score += 15
    elif 45 <= rsi < 50 or 65 < rsi <= 70:     score += 9
    elif 40 <= rsi < 45:                        score += 4

    # Minervini / Weinstein premium (20 pts)
    mv = c.get("minervini_score", 0)
    score += mv * 2                              # 0-14
    if c.get("weinstein_stage") == 2:            score += 6
    if c.get("vcp"):                             score += 6
    elif c.get("spring"):                        score += 5
    if c.get("avwap"):                           score += 3
    rs_rank = c.get("rs_rank", 50)
    if rs_rank >= 80:    score += 5
    elif rs_rank >= 70:  score += 2

    # Catalyst + Narrative (20 pts)
    if c.get("catalyst"):          score += 12
    if c.get("narrative_sector"):  score += 8

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

        # Hard filters — relaxed to match prescreen (35-73 RSI, MA50 ±5%)
        if not (35 <= rsi <= 73):
            return None
        ma50_val = tech.get("ma50", 0)
        if ma50_val and (price / ma50_val - 1) * 100 < -5:
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

        # Volume spike + momentum from prescreen
        vol_spike = prescreen.get("vol_spike", 1.0) if prescreen else 1.0
        rs_vs_qqq = prescreen.get("rs_vs_qqq", 0.0) if prescreen else 0.0
        rs_rank   = prescreen.get("rs_rank", 50) if prescreen else 50

        # ── Advanced scoring: Minervini / Weinstein / VCP / Wyckoff / AVWAP ────
        minervini_score, _ = _minervini_template(closes)
        weinstein_stage    = _weinstein_stage(hist)
        vols_list          = hist["Volume"].tolist() if "Volume" in hist.columns else None
        vcp                = _vcp_detect(closes, vols_list)
        spring             = _wyckoff_spring(closes, vols_list)
        avwap              = _avwap_support(hist)
        multi_tf           = _multi_tf_confirmation(closes, hist)
        canslim            = _canslim_score(closes, rs_rank=rs_rank, vol_spike=vol_spike,
                                            avwap=avwap)
        setup_grade, setup_score = _calc_setup_grade(
            minervini_score, weinstein_stage, canslim, vcp, spring, multi_tf, avwap,
            rr.get("rr_ratio", 0)
        )

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
            "rs_rank":          rs_rank,
            "ma50_slope_up":    slope_up,
            "above_ma200":      bool(tech.get("above_ma200")),
            "pct_from_ma50":    pattern.get("pct_from_ma50", 0),
            "catalyst":         catalyst,
            "narrative_sector": narrative_sector,
            "source":           source,
            # Minervini / Weinstein / CAN SLIM fields
            "minervini_score":  minervini_score,
            "weinstein_stage":  weinstein_stage,
            "vcp":              vcp,
            "spring":           spring,
            "avwap":            avwap,
            "multi_tf":         multi_tf,
            "canslim_score":    canslim,
            "setup_grade":      setup_grade,
            "setup_score":      setup_score,
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
    "Jesteś doświadczonym swing traderem. Oceniasz setupy techniczne.\n"
    "Dla każdego kandydata podaj ocenę 1-10 (10 = najlepszy setup tygodnia).\n"
    "Oceniaj na podstawie: jakości patternu, R/R, momentum, katalizatora.\n"
    "Makro to tylko kontekst — NIE odrzucaj setupów, zawsze oceniaj wszystkich.\n"
    "ODPOWIADASZ TYLKO W JSON (tablica, wszystkich kandydatów z oceną):\n"
    '[{"ticker":"...","rating":8,"pattern":"...","entry":0.0,"target":0.0,"stop":0.0,'
    '"target_pct":0.0,"stop_pct":0.0,"rr":0.0,'
    '"window_days":7,"catalyst":"...","reason":"1 zdanie po polsku"}]'
)


def _pick_top_setups(candidates: list[dict], macro: dict, limit: int = 5) -> list[dict]:
    if not candidates:
        return []

    # Sort by score, take top 20 for Claude to rate
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
        f"Oceń każdy setup od 1-10 i zwróć JSON dla WSZYSTKICH {len(top20)} kandydatów:\n"
        + "\n".join(lines)
    )

    try:
        resp = _ctx.claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=3000,
            system=_SWING_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        m   = re.search(r"\[.*\]", raw, re.DOTALL)
        rated = json.loads(m.group() if m else "[]")
        # Sort by Claude's rating, return top_n — only if Claude actually rated anything
        if rated:
            rated.sort(key=lambda x: x.get("rating", 0), reverse=True)
            return rated[:top_n]
        logger.warning("Claude returned empty ratings list, using score-based fallback")
    except Exception as e:
        logger.warning("Pick top setups error: %s", e)

    # Fallback: return top-scored candidates directly without Claude rating
    result = []
    for c in top20[:top_n]:
        rr  = c.get("rr", {})
        pat = c.get("pattern", {})
        result.append({
            "ticker":      c["ticker"],
            "rating":      round(c.get("score", 50) / 10, 1),
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
    return result


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

    rating = s.get("rating")
    rating_str = f"  ⭐ {rating}/10" if rating else ""
    header_text = f"*{i}. {ticker}* — _{s.get('pattern','?')}_{rating_str}"
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

    # Minervini / Weinstein / grade line (from candidate_data)
    cd = candidate_data or {}
    grade      = cd.get("setup_grade") or s.get("setup_grade")
    sscore     = cd.get("setup_score") or s.get("setup_score")
    mv_score   = cd.get("minervini_score")
    ws_stage   = cd.get("weinstein_stage")
    rs_rank    = cd.get("rs_rank") or s.get("rs_rank")
    vcp_flag   = cd.get("vcp") or s.get("vcp")
    spring_flag= cd.get("spring") or s.get("spring")
    avwap_flag = cd.get("avwap") or s.get("avwap")
    if grade:
        grade_line_parts = []
        if grade and sscore is not None:
            grade_line_parts.append(f"Grade: *{grade}* ({sscore}/28)")
        if mv_score is not None:
            grade_line_parts.append(f"Minervini: {mv_score}/7")
        if ws_stage:
            stage_emoji = "✅" if ws_stage == 2 else "⚠️"
            grade_line_parts.append(f"Weinstein: Stage {ws_stage} {stage_emoji}")
        if rs_rank:
            grade_line_parts.append(f"RS: {rs_rank}/100")
        pattern_flags = []
        if vcp_flag:    pattern_flags.append("VCP")
        if spring_flag: pattern_flags.append("Spring")
        if avwap_flag:  pattern_flags.append("AVWAP✓")
        if pattern_flags:
            grade_line_parts.append(" · ".join(pattern_flags))
        if grade_line_parts:
            blocks.append({"type": "context",
                            "elements": [{"type": "mrkdwn",
                                          "text": "  ·  ".join(grade_line_parts)}]})

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

        # Compute RS composite rank (1-100) across all prescreened tickers
        # RS = 5d*0.4 + 20d*0.35 + 30d*0.25 (Van Tharp relative strength)
        if prescreened:
            for p in prescreened:
                p["_rs_raw"] = (
                    p.get("r5d", 0) * 0.40 +
                    p.get("r20d", 0) * 0.35 +
                    p.get("momentum_30d", 0) * 0.25
                )
            sorted_rs = sorted(prescreened, key=lambda x: x["_rs_raw"])
            n_total = len(sorted_rs)
            for rank_i, p in enumerate(sorted_rs):
                p["rs_rank"] = round((rank_i + 1) / n_total * 100)

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
        return len(setups)
    except Exception as e:
        logger.error("send_weekly_setups failed: %s", e)
        return 0


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
